#!/usr/bin/env python3
"""Soccer Wearhouse — collections health + automation-candidate audit.

Pinned to API 2026-07: Collection.sources / CollectionConditionsSource only
exist there. Read-only; suggested rules are applied by a human in admin.

  python collections_audit.py                 fetch + audit + save baseline
  python collections_audit.py --cached        re-audit the last downloaded JSONL
  python collections_audit.py --members       also pull collection membership (2nd bulk op)
                                              and compute Adds / Count After per suggestion
  python collections_audit.py --deliverable   also write the merchant XLSX (+ PDF); best
                                              with --members, otherwise Adds stays blank
  python collections_audit.py --force         cancel in-flight bulk query op first
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

import config
import history
import report
import runlog
from catalog import load_products
from matcher import match_title, normalize
from rules import FILTER_HANDLES, load_task_rules
from shopify_client import ShopifyBulk, get_admin_token

COLLECTIONS_BULK_QUERY = '''
{
  collections {
    edges {
      node {
        id
        title
        handle
        updatedAt
        productsCount { count }
        sources {
          __typename
          ... on CollectionConditionsSource {
            inclusion {
              matchType
              conditions {
                __typename
                ... on CollectionSourceInclusionConditionMetafieldString {
                  definition { namespace key }
                  relation
                  values
                }
                ... on CollectionSourceInclusionConditionMetafieldStringList {
                  definition { namespace key }
                  relation
                  values
                }
                ... on CollectionSourceInclusionConditionProductType {
                  relation
                  values
                }
                ... on CollectionSourceInclusionConditionProductTag {
                  relation
                  values
                }
              }
            }
          }
        }
      }
    }
  }
}'''

MEMBERS_BULK_QUERY = """
{
  collections {
    edges {
      node {
        id
        products {
          edges {
            node {
              id
            }
          }
        }
      }
    }
  }
}"""

KEEP_MANUAL = ("sale", "clearance", "black friday", "cyber", "gift", "new arrival",
               "best seller", "copa america", "euro", "world cup", "champions league")
COLOURS = ("black", "white", "blue", "red", "green", "yellow", "orange", "purple", "pink",
           "grey", "gray", "gold", "silver", "navy", "volt", "turquoise", "aqua", "maroon")
FOOTWEAR_WORDS = ("cleats", "cleat", "shoes", "boots", "footwear")
TYPE_WORDS = {
    "jerseys": "Jerseys", "jersey": "Jerseys", "hats": "Hats", "hat": "Hats",
    "beanies": "Hats", "snapbacks": "Hats", "scarves": "Scarves", "scarf": "Scarves",
    "posters": "Posters", "poster": "Posters", "hoodies": "Hoodies", "hoodie": "Hoodies",
    "jackets": "Jackets", "jacket": "Jackets", "t-shirts": "T-Shirts", "t-shirt": "T-Shirts",
    "tshirts": "T-Shirts", "tees": "T-Shirts", "shorts": "Shorts", "socks": "Socks",
    "sweaters": "Sweaters", "pants": "Pants", "balls": "Balls", "bags": "Bags",
    "backpacks": "Bags", "cleats": "Footwear", "shoes": "Footwear", "footwear": "Footwear",
}
BROAD_TAIL = ("&", " and ", ",", "accessor", "gear", "collection", "merch", "apparel")
CANDIDATE_ROW_FIELDS = ["title", "handle", "count", "classification", "suggested_rule",
                        "admin_url", "rule_type", "adds", "count_after"]
_SUFFIX = re.compile(r"\s+(soccer\s+)?(jerseys?|gear|accessories|accessory|patches|collection|apparel|merch(andise)?)(\s*([&,+]|and)\s*.*)?$",
                     re.IGNORECASE)

_TYPE_WORDS_NORM = {normalize(k): v for k, v in TYPE_WORDS.items()}
_SUBCAT_QUALIFIERS = {"official", "premium"}


def _singular(word):
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _subcat_vocab(cfg, products_ctx):
    tags = {s.strip() for s in ((cfg or {}).get("subcat") or {}).get("addon_eligible_subcats") or []
            if s.strip()}
    for p in (products_ctx or {}).get("active", []):
        for t in p["tags"]:
            if t.startswith("SubCat_"):
                tags.add(t)
    return tags


def _subcat_for(core_words, vocab):
    hits = []
    for tag in vocab:
        tw = {_singular(w) for w in normalize(tag[len("SubCat_"):]).split()}
        if not tw:
            continue
        if core_words <= tw:
            hits.append(tag)
        elif tw <= core_words and (core_words - tw) <= _SUBCAT_QUALIFIERS:
            hits.append(tag)
    if len(hits) == 1:
        return hits[0], False
    return None, len(hits) > 1


_COND_KINDS = {
    "CollectionSourceInclusionConditionMetafieldString": "metafield",
    "CollectionSourceInclusionConditionMetafieldStringList": "metafield",
    "CollectionSourceInclusionConditionProductType": "type",
    "CollectionSourceInclusionConditionProductTag": "tag",
}


def _cond_kind(cond):
    return _COND_KINDS.get(cond.get("__typename"), "other")


FILTER_KEYS = ("club_filter", "country_filter", "player_filter")


def _product_matches_conditions(p, c):
    """True/False when every condition is understood; None = can't judge."""
    conds = c["conditions"]
    if not conds:
        return None
    results = []
    for cond in conds:
        kind = _cond_kind(cond)
        rel = cond.get("relation")
        vals = cond.get("values") or []
        if kind == "type":
            if rel not in (None, "EQUALS"):
                return None
            results.append(any(p["type"].casefold() == v.casefold() for v in vals))
        elif kind == "tag":
            if rel not in (None, "TAGGED_WITH"):
                return None
            ptags = {t.casefold() for t in p["tags"]}
            results.append(any(v.casefold() in ptags for v in vals))
        elif kind == "metafield":
            d = cond.get("definition") or {}
            fk = d.get("key")
            if d.get("namespace") != "custom" or fk not in FILTER_KEYS or rel not in (None, "EQUALS"):
                return None
            results.append(p.get(fk) in vals)
        else:
            return None
    return all(results) if c.get("match_type") == "ALL" else any(results)


def explain_departures(collections, members, prev_members, products_by_gid):
    rows, inline = [], {}
    for c in collections:
        prev = prev_members.get(c["gid"])
        if prev is None:
            continue
        now = members.get(c["gid"], set())
        departed = prev - now
        if not departed:
            continue
        joined = len(now - prev)
        counts, examples = Counter(), []
        for gid in sorted(departed):
            p = products_by_gid.get(gid)
            if p is None:
                status, title, handle, matches = "DELETED", "", "", ""
            else:
                status, title, handle = p["status"], p["title"], p["handle"]
                m = _product_matches_conditions(p, c) if c["has_rule"] else None
                matches = {True: "yes", False: "no"}.get(m, "")
            if status == "DELETED":
                counts["deleted"] += 1
            elif matches == "no":
                counts["rule-mismatch"] += 1
            elif status == "ACTIVE":
                counts["still ACTIVE"] += 1
            else:
                counts["other"] += 1
            if title and len(examples) < 5:
                examples.append(title)
            rows.append({"collection_title": c["title"], "collection_id": c["id"],
                         "product_gid": gid, "handle": handle, "title": title,
                         "status_now": status, "still_matches_rule": matches,
                         "admin_url": config.admin_product_url(gid.rsplit("/", 1)[-1])})
        parts = " \u00b7 ".join(f"{k} {counts[k]}"
                                for k in ("deleted", "still ACTIVE", "rule-mismatch", "other")
                                if counts.get(k))
        ex = ""
        if examples:
            more = len(departed) - len(examples)
            ex = " \u00b7 e.g. " + ", ".join(examples) + (f" +{more} more" if more > 0 else "")
        inline[c["handle"]] = (f"-{len(departed) - joined} net (departed {len(departed)}, "
                               f"joined {joined}) \u00b7 {parts}{ex}")
    return rows, inline


def load_collections(jsonl_path):
    out = []
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not obj.get("id", "").startswith("gid://shopify/Collection/"):
                continue
            sources = obj.get("sources") or []
            conditions, match_type = [], None
            for s in sources:
                inclusion = s.get("inclusion") or {}
                if match_type is None and inclusion.get("matchType"):
                    match_type = inclusion["matchType"]
                for c in inclusion.get("conditions") or []:
                    conditions.append(c)
            out.append({
                "gid": obj["id"],
                "id": obj["id"].rsplit("/", 1)[-1],
                "title": obj.get("title", "") or "",
                "handle": obj.get("handle", "") or "",
                "count": ((obj.get("productsCount") or {}).get("count")) or 0,
                "sources_n": len(sources),
                "match_type": match_type,
                "conditions": conditions,
                "has_rule": bool(conditions),
            })
    return out


def load_baseline():
    if not os.path.exists(config.COLLECTIONS_BASELINE):
        return None
    with open(config.COLLECTIONS_BASELINE, encoding="utf-8") as fh:
        return json.load(fh)


def save_baseline(collections, log=print):
    os.makedirs(os.path.dirname(config.COLLECTIONS_BASELINE) or ".", exist_ok=True)
    data = {
        "saved_at": config.now().isoformat(timespec="seconds"),
        "collections": {c["gid"]: {"title": c["title"], "count": c["count"], "has_rule": c["has_rule"]}
                        for c in collections},
    }
    with open(config.COLLECTIONS_BASELINE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    dated_dir = os.path.join(config.summaries_dir(), "baselines")
    os.makedirs(dated_dir, exist_ok=True)
    dated = os.path.join(dated_dir, f"collections-baseline-{config.now():%Y%m%d-%H%M}.json")
    with open(dated, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    for name in sorted(os.listdir(dated_dir))[:-15]:
        os.remove(os.path.join(dated_dir, name))
    log(f"  baseline saved ({len(collections)} collections) -> {config.COLLECTIONS_BASELINE}")


def _metafield_vocab_from_cache():
    if not os.path.exists(config.CACHE_PRODUCTS_JSONL):
        return set()
    vocab = set()
    with open(config.CACHE_PRODUCTS_JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or '"__parentId"' not in line:
                continue
            obj = json.loads(line)
            v = (obj.get("value") or "").strip()
            if not v:
                continue
            vocab.add(v)
            if v.startswith("["):
                try:
                    vocab.update(str(x).strip() for x in json.loads(v))
                except ValueError:
                    pass
    return vocab


def load_members(jsonl_path):
    members = defaultdict(set)
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            oid, parent = obj.get("id", ""), obj.get("__parentId")
            if parent and oid.startswith("gid://shopify/Product/"):
                members[parent].add(oid)
            elif oid.startswith("gid://shopify/Collection/"):
                members.setdefault(oid, set())
    return members


def load_products_ctx(log=print):
    """Products cache context for whole-type detection and Adds math. None if absent."""
    if not os.path.exists(config.CACHE_PRODUCTS_JSONL):
        log("  products cache missing — whole-type detection and Adds are limited "
            "(run audit.py first).")
        return None
    active = [p for p in load_products(config.CACHE_PRODUCTS_JSONL) if p["status"] == "ACTIVE"]
    by_type = defaultdict(int)
    for p in active:
        by_type[p["type"]] += 1
    return {"active": active, "type_counts": dict(by_type)}


def _warn_cache_drift(log, paths=None, max_hours=6):
    paths = paths or [config.CACHE_PRODUCTS_JSONL, config.CACHE_COLLECTIONS_JSONL,
                      config.CACHE_COLLECTIONS_MEMBERS]
    stamps = [(os.path.basename(p), os.path.getmtime(p)) for p in paths if os.path.exists(p)]
    if len(stamps) < 2:
        return False
    times = [s for _, s in stamps]
    hours = (max(times) - min(times)) / 3600
    if hours <= max_hours:
        return False
    names = ", ".join(f"{n} ({datetime.datetime.fromtimestamp(s):%m-%d %H:%M})" for n, s in stamps)
    log(f"  WARNING: caches are {hours:.1f}h apart ({names}) — Adds/Count After span "
        "drifted data; refresh products and collections the same morning for merchant numbers.")
    return True


def health_check(collections, baseline, cfg, products_ctx=None):
    type_set = tag_set = None
    if products_ctx:
        type_set = {t.casefold() for t in products_ctx["type_counts"]}
        tag_set = {t.casefold() for p in products_ctx["active"] for t in p["tags"]}
    vocab = set()
    canon_by_key = {}
    for filter_key, handle in FILTER_HANDLES:
        entry = (cfg or {}).get(handle) or {}
        canon = entry.get("_canonicals", set())
        canon_by_key[filter_key] = canon or None
        vocab |= canon
    vocab |= _metafield_vocab_from_cache()
    prev = (baseline or {}).get("collections", {})
    rows = []

    def add(c, finding, detail=""):
        rows.append({"title": c["title"], "handle": c["handle"], "count": c["count"],
                     "finding": finding, "detail": detail,
                     "admin_url": config.admin_collection_url(c["id"])})

    for c in collections:
        p = prev.get(c["gid"])
        if p and p["has_rule"] and not c["has_rule"]:
            add(c, "RULE_LOST", f"had a rule on {baseline.get('saved_at', '')}")
        if p and c["count"] < p["count"]:
            add(c, "COUNT_DROP", f"{p['count']} -> {c['count']}")
        if c["has_rule"] and c["count"] == 0:
            add(c, "ZERO_WITH_RULE")
        if c["sources_n"] > 1:
            add(c, "MULTI_SOURCE", f"{c['sources_n']} sources")
        for cond in c["conditions"]:
            kind = _cond_kind(cond)
            for v in cond.get("values") or []:
                if not v:
                    continue
                if kind == "metafield":
                    d = cond.get("definition") or {}
                    fk = d.get("key") if d.get("namespace") == "custom" else None
                    own = canon_by_key.get(fk)
                    if own is not None:
                        if v not in own:
                            add(c, "UNKNOWN_RULE_VALUE",
                                f"metafield value '{v}' is not a {fk} canonical")
                    elif vocab and v not in vocab:
                        add(c, "UNKNOWN_RULE_VALUE", f"metafield value '{v}'")
                elif kind == "type" and type_set is not None and v.casefold() not in type_set:
                    add(c, "UNKNOWN_RULE_VALUE", f"type '{v}' matches no active product type")
                elif kind == "tag" and tag_set is not None and v.casefold() not in tag_set:
                    add(c, "UNKNOWN_RULE_VALUE", f"tag '{v}' on no active product")
    return rows


def _strip_suffix(title):
    core, broad = title, False
    for _ in range(3):
        m = _SUFFIX.search(core)
        if not m:
            break
        removed = m.group(0).lower()
        if any(b in removed for b in BROAD_TAIL):
            broad = True
        core = core[:m.start()].strip()
    return core, broad


def _review(c, detail):
    return ("REVIEW", "Review", "", detail)


def _classify_candidate(c, cfg, products_ctx, members=None):
    title = c["title"]
    title_l = title.lower()
    core, broad = _strip_suffix(title)
    core_norm = normalize(core)

    if any(k in title_l for k in KEEP_MANUAL):
        return ("KEEP_MANUAL", "", "", ""), None
    if "jersey:" in title_l:
        return _review(c, "player PDP-style page"), None
    if "custom" in title_l:
        return _review(c, "custom product page"), None
    if any(ch in title for ch in "\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00fc\u00e7\u00e0\u00e8\u00f6") \
            or "." in core or " de " in f" {core.lower()} ":
        return _review(c, "accented/period/particle name \u2014 verify canonical value manually"), None

    vendors = {normalize(v) for v in ((cfg or {}).get("normalize") or {}).get("vendor_map", {}).values()}
    if core_norm and core_norm in vendors:
        return _review(c, "brand collection \u2014 vendor rules out of scope"), None

    colour = next((w for w in COLOURS if f" {w} " in f" {core_norm} "), None)
    if colour and any(w in core_norm.split() for w in FOOTWEAR_WORDS):
        rule = {"kind": "colour", "type": "Footwear", "colour": colour}
        return ("COLOUR_VERIFY", "Title colour",
                f"Product type equals 'Footwear' AND Title contains '{colour}'", ""), rule

    matches = []
    for _, handle in FILTER_HANDLES:
        comp = ((cfg or {}).get(handle) or {}).get("_compiled")
        if not comp:
            continue
        best, ambiguous = match_title(core, comp)
        if ambiguous:
            matches.append((handle, None))
        elif best:
            matches.append((handle, best))
    real = [(h, v) for h, v in matches if v]
    if len(real) == 1 and len(matches) == 1:
        handle, value = real[0]
        residue = f" {core_norm} "
        for r_ in ((cfg or {}).get(handle) or {}).get("keyword_rules") or []:
            if (r_.get("canonical_value") or "").strip() == value:
                for kw in r_.get("safe_keywords") or []:
                    residue = residue.replace(f" {normalize(kw)} ", " ")
        fillers = {"fc", "cf", "afc", "sc", "club", "de", "the", "soccer"}
        leftover = [t for t in residue.split()
                    if t not in fillers and not t.isdigit() and t not in TYPE_WORDS
                    and t not in COLOURS]
        if leftover:
            return _review(c, f"partial entity match ('{value}') \u2014 unexplained words: "
                              f"{' '.join(leftover)}"), None
        filter_key = f"{handle}_filter"
        type_word = next((t for w, t in TYPE_WORDS.items()
                          if f" {w} " in f" {title_l} ".replace("-", " ")), None)
        label = handle.capitalize() + " (metafield)"
        if type_word and not broad:
            rule = {"kind": "metafield", "filter_key": filter_key, "value": value,
                    "type": type_word}
            return ("AUTOMATABLE", "Title + Type",
                    f"custom.{filter_key} equals '{value}' AND Product type equals "
                    f"'{type_word}' (additive OR rule; keep manual selections)", ""), rule
        rule = {"kind": "metafield", "filter_key": filter_key, "value": value}
        return ("AUTOMATABLE", label,
                f"custom.{filter_key} equals '{value}' (additive OR rule; keep manual "
                f"selections)", ""), rule
    if matches:
        return _review(c, "multiple/ambiguous entity matches"), None

    if products_ctx:
        wt_norm = normalize(title)
        wt_sans = " ".join(w for w in wt_norm.split() if w != "soccer")
        whole = None
        for cand in (wt_norm, wt_sans):
            if not cand:
                continue
            whole = _TYPE_WORDS_NORM.get(cand) or next(
                (t for t in products_ctx["type_counts"] if normalize(t) == cand), None)
            if whole:
                break
        if whole is None:
            toks = [t for t in wt_sans.split() if t != "and"]
            mapped = {_TYPE_WORDS_NORM.get(t) for t in toks}
            if toks and len(mapped) == 1 and None not in mapped:
                whole = mapped.pop()
        if whole:
            total = products_ctx["type_counts"].get(whole, 0)
            covered = c["count"]
            if members is not None:
                type_gids = {p["gid"] for p in products_ctx["active"] if p["type"] == whole}
                covered = len(members.get(c["gid"], set()) & type_gids)
            if total and covered >= 0.5 * total:
                rule = {"kind": "whole_type", "type": whole}
                return ("AUTOMATABLE", "Product type",
                        f"Product type equals '{whole}' (collection holds {c['count']}; "
                        f"store has {total} active {whole} products)", ""), rule
            if total:
                return _review(c, f"type-named but only {covered} of {total} active "
                                  f"{whole} products are members \u2014 curated subset"), None
        vocab = _subcat_vocab(cfg, products_ctx)
        if vocab:
            core_words = {_singular(w) for w in wt_sans.split() if w != "and"}
            tag, ambiguous = _subcat_for(core_words, vocab)
            if tag:
                rule = {"kind": "subcat", "tag": tag}
                return ("AUTOMATABLE", "Category tag",
                        f"Tag contains '{tag}' (additive; keep manual selections)", ""), rule
            if ambiguous:
                return _review(c, "ambiguous SubCat mapping \u2014 several tags fit"), None
    return _review(c, "no decomposition \u2014 likely thematic/custom"), None


def _rule_matches(rule, p):
    if rule["kind"] == "metafield":
        if p.get(rule["filter_key"]) != rule["value"]:
            return False
        return p["type"] == rule["type"] if "type" in rule else True
    if rule["kind"] == "whole_type":
        return p["type"] == rule["type"]
    if rule["kind"] == "subcat":
        return rule["tag"] in p["tags"]
    if rule["kind"] == "colour":
        return p["type"] == rule["type"] and rule["colour"] in p["title"].lower()
    return False


def find_candidates(collections, cfg, products_ctx=None, members=None):
    rows = []
    for c in collections:
        if c["has_rule"] or c["count"] == 0:
            continue
        (classification, rule_type, suggested, detail), rule = _classify_candidate(c, cfg, products_ctx, members)
        adds = count_after = ""
        if rule and products_ctx and members is not None:
            member_ids = members.get(c["gid"], set())
            adds = sum(1 for p in products_ctx["active"]
                       if _rule_matches(rule, p) and p["gid"] not in member_ids)
            count_after = c["count"] + adds
        rows.append({"title": c["title"], "handle": c["handle"], "count": c["count"],
                     "classification": classification,
                     "suggested_rule": suggested or detail,
                     "admin_url": config.admin_collection_url(c["id"]),
                     "rule_type": rule_type, "adds": adds, "count_after": count_after,
                     "_payload": ({"collection_gid": c["gid"], "rule": rule}
                                  if rule else None)})
    order = {"AUTOMATABLE": 0, "COLOUR_VERIFY": 1, "REVIEW": 2, "KEEP_MANUAL": 3}
    rows.sort(key=lambda r: (order[r["classification"]],
                             -(r["adds"] if isinstance(r["adds"], int) else -1),
                             -r["count"]))
    return rows


def pipeline(cached=False, force=False, members_pull=False, deliverable=False, stamp=None, log=print):
    cfg = load_task_rules(config.TASK_CONFIGS)
    if not cached:
        client = ShopifyBulk(config.SHOP_DOMAIN, get_admin_token(log), config.COLLECTIONS_API_VERSION)
        log(f"Kicking off collections bulk operation (API {config.COLLECTIONS_API_VERSION})...")
        client.run_to_file(COLLECTIONS_BULK_QUERY, config.CACHE_COLLECTIONS_JSONL, force=force, log=log)
        if members_pull:
            if os.path.exists(config.CACHE_COLLECTIONS_MEMBERS):
                shutil.copyfile(config.CACHE_COLLECTIONS_MEMBERS,
                                config.CACHE_COLLECTIONS_MEMBERS + ".prev")
            log("Kicking off collection members bulk operation...")
            client.run_to_file(MEMBERS_BULK_QUERY, config.CACHE_COLLECTIONS_MEMBERS, force=force, log=log)
    if not os.path.exists(config.CACHE_COLLECTIONS_JSONL):
        sys.exit(f"No cached JSONL at {config.CACHE_COLLECTIONS_JSONL}. Run without --cached first.")

    collections = load_collections(config.CACHE_COLLECTIONS_JSONL)
    log(f"  {len(collections)} collections ({sum(1 for c in collections if c['has_rule'])} with rules)")
    baseline = load_baseline()
    if not baseline:
        log("  no baseline yet — this run establishes it; diffs start next run.")

    members = None
    if members_pull or deliverable:
        if os.path.exists(config.CACHE_COLLECTIONS_MEMBERS):
            members = load_members(config.CACHE_COLLECTIONS_MEMBERS)
            log(f"  members cache: {sum(len(m) for m in members.values()):,} memberships "
                f"across {len(members)} collections")
        else:
            log("  members cache missing — Adds/Count After stay blank "
                "(run with --members, without --cached, to pull it).")
    products_ctx = load_products_ctx(log=log)
    if members is not None:
        _warn_cache_drift(log)

    stamp = stamp or config.now().strftime("%Y%m%d-%H%M")
    health = health_check(collections, baseline, cfg, products_ctx=products_ctx)
    prev_path = config.CACHE_COLLECTIONS_MEMBERS + ".prev"
    if members is not None and os.path.exists(prev_path) \
            and os.path.exists(config.CACHE_PRODUCTS_JSONL):
        products_by_gid = {p["gid"]: p for p in load_products(config.CACHE_PRODUCTS_JSONL)}
        dep_rows, dep_inline = explain_departures(collections, members,
                                                  load_members(prev_path), products_by_gid)
        if dep_rows:
            report.write_rows_csv(
                os.path.join(config.run_dir(stamp), f"collections-departures-{stamp}.csv"),
                dep_rows, ["collection_title", "collection_id", "product_gid", "handle",
                           "title", "status_now", "still_matches_rule", "admin_url"])
            log(f"  departures: {len(dep_rows)} product(s) left {len(dep_inline)} collection(s)")
            for row in health:
                if row["finding"] == "COUNT_DROP" and row["handle"] in dep_inline:
                    row["detail"] = f"{row['detail']}: {dep_inline[row['handle']]}"
    if health:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"collections-health-{stamp}.csv"),
                              health, ["title", "handle", "count", "finding", "detail", "admin_url"])
    log(f"  health findings: {len(health)}")

    candidates = find_candidates(collections, cfg, products_ctx=products_ctx, members=members)
    import deliverable as dlv
    if candidates:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"collections-candidates-{stamp}.csv"),
                              [{k: v for k, v in r.items() if k != "_payload"} for r in candidates],
                              CANDIDATE_ROW_FIELDS)
        dlv.build_rules_payload(candidates, stamp, log=log)
    counts = {}
    for r in candidates:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    log("  candidates: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if deliverable:
        dlv.build_xlsx(candidates, stamp, log=log)
        dlv.build_pdf(candidates, stamp, log=log)

    alerts = {}
    for r in health:
        if r["finding"] in ("RULE_LOST", "COUNT_DROP"):
            alerts[r["finding"]] = alerts.get(r["finding"], 0) + 1
    health_status = ("ATTENTION — " + "  ".join(f"{k}={v}" for k, v in sorted(alerts.items()))
                     ) if alerts else "OK"
    log("  HEALTH: " + health_status)
    ruled_kinds = Counter(_cond_kind(cond) for c in collections for cond in c["conditions"])
    applied = {}
    for c in collections:
        kinds = {_cond_kind(cond) for cond in c["conditions"]}
        if "type" in kinds or "tag" in kinds:
            applied[c["id"]] = c["count"]
        elif "metafield" in kinds:
            for cond in c["conditions"]:
                d = cond.get("definition") or {}
                if d.get("namespace") == "custom" and d.get("key") in (
                        "club_filter", "country_filter", "player_filter"):
                    applied[c["id"]] = c["count"]
                    break
    extra = {"cached": cached,
             "cache_pulled_at": datetime.datetime.fromtimestamp(
                 os.path.getmtime(config.CACHE_COLLECTIONS_JSONL),
                 tz=config.STAMP_TZ).isoformat(timespec="seconds"),
             "task_configs_date": (cfg or {}).get("_meta", {}).get("file_date"),
             "health_by_code": dict(Counter(r["finding"] for r in health)),
             "ruled_by_kind": dict(ruled_kinds),
             "applied_members": applied}
    if members is not None:
        extra["memberships"] = sum(len(m) for m in members.values())
        if os.path.exists(config.CACHE_COLLECTIONS_MEMBERS):
            extra["members_pulled_at"] = datetime.datetime.fromtimestamp(
                os.path.getmtime(config.CACHE_COLLECTIONS_MEMBERS),
                tz=config.STAMP_TZ).isoformat(timespec="seconds")
    history.append_collections(stamp, collections, candidates_counts=counts,
                               health_findings=len(health), health=health_status, extra=extra)
    if not cached or not baseline:
        save_baseline(collections, log=log)
    else:
        log("  baseline unchanged (cached re-scan).")
    log("Done.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cached", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--members", action="store_true",
                    help="also pull collection membership and compute Adds / Count After")
    ap.add_argument("--deliverable", action="store_true",
                    help="also write the merchant XLSX (+ PDF if reportlab is installed)")
    args = ap.parse_args()
    stamp = config.now().strftime("%Y%m%d-%H%M")
    runlog.run(stamp, lambda log: pipeline(cached=args.cached, force=args.force,
                                           members_pull=args.members,
                                           deliverable=args.deliverable,
                                           stamp=stamp, log=log))


if __name__ == "__main__":
    main()