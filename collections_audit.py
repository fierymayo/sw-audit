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
import sys
from collections import defaultdict

import config
import report
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
              conditions {
                __typename
                ... on CollectionSourceInclusionConditionMetafieldString {
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
            conditions = []
            for s in sources:
                for c in (s.get("inclusion") or {}).get("conditions") or []:
                    conditions.append(c)
            out.append({
                "gid": obj["id"],
                "id": obj["id"].rsplit("/", 1)[-1],
                "title": obj.get("title", "") or "",
                "handle": obj.get("handle", "") or "",
                "count": ((obj.get("productsCount") or {}).get("count")) or 0,
                "sources_n": len(sources),
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
    os.makedirs(config.OUT_DIR, exist_ok=True)
    data = {
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "collections": {c["gid"]: {"title": c["title"], "count": c["count"], "has_rule": c["has_rule"]}
                        for c in collections},
    }
    with open(config.COLLECTIONS_BASELINE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
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


def health_check(collections, baseline, cfg):
    vocab = set()
    for _, handle in FILTER_HANDLES:
        entry = (cfg or {}).get(handle) or {}
        vocab |= entry.get("_canonicals", set())
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
        if vocab:
            for cond in c["conditions"]:
                for v in cond.get("values") or []:
                    if v and v not in vocab:
                        add(c, "UNKNOWN_RULE_VALUE", v)
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


def _classify_candidate(c, cfg, products_ctx):
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
        whole = TYPE_WORDS.get(core_norm) or next(
            (t for t in products_ctx["type_counts"] if normalize(t) == core_norm), None)
        if whole:
            total = products_ctx["type_counts"].get(whole, 0)
            if total and c["count"] >= 0.5 * total:
                rule = {"kind": "whole_type", "type": whole}
                return ("AUTOMATABLE", "Product type",
                        f"Product type equals '{whole}' (collection holds {c['count']}; "
                        f"store has {total} active {whole} products)", ""), rule
            if total:
                return _review(c, f"type-named but only {c['count']} of {total} active "
                                  f"{whole} products \u2014 curated subset"), None
    return _review(c, "no decomposition \u2014 likely thematic/custom"), None


def _rule_matches(rule, p):
    if rule["kind"] == "metafield":
        if p.get(rule["filter_key"]) != rule["value"]:
            return False
        return p["type"] == rule["type"] if "type" in rule else True
    if rule["kind"] == "whole_type":
        return p["type"] == rule["type"]
    if rule["kind"] == "colour":
        return p["type"] == rule["type"] and rule["colour"] in p["title"].lower()
    return False


def find_candidates(collections, cfg, products_ctx=None, members=None):
    rows = []
    for c in collections:
        if c["has_rule"] or c["count"] == 0:
            continue
        (classification, rule_type, suggested, detail), rule = _classify_candidate(c, cfg, products_ctx)
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
                     "rule_type": rule_type, "adds": adds, "count_after": count_after})
    order = {"AUTOMATABLE": 0, "COLOUR_VERIFY": 1, "REVIEW": 2, "KEEP_MANUAL": 3}
    rows.sort(key=lambda r: (order[r["classification"]],
                             -(r["adds"] if isinstance(r["adds"], int) else -1),
                             -r["count"]))
    return rows


def pipeline(cached=False, force=False, members_pull=False, deliverable=False, log=print):
    cfg = load_task_rules(config.TASK_CONFIGS)
    if not cached:
        client = ShopifyBulk(config.SHOP_DOMAIN, get_admin_token(log), config.COLLECTIONS_API_VERSION)
        log(f"Kicking off collections bulk operation (API {config.COLLECTIONS_API_VERSION})...")
        client.run_to_file(COLLECTIONS_BULK_QUERY, config.CACHE_COLLECTIONS_JSONL, force=force, log=log)
        if members_pull:
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

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    health = health_check(collections, baseline, cfg)
    if health:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"collections-health-{stamp}.csv"),
                              health, ["title", "handle", "count", "finding", "detail", "admin_url"])
    log(f"  health findings: {len(health)}")

    candidates = find_candidates(collections, cfg, products_ctx=products_ctx, members=members)
    if candidates:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"collections-candidates-{stamp}.csv"),
                              candidates, CANDIDATE_ROW_FIELDS)
    counts = {}
    for r in candidates:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    log("  candidates: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if deliverable:
        import deliverable as dlv
        dlv.build_xlsx(candidates, stamp, log=log)
        dlv.build_pdf(candidates, stamp, log=log)

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
    pipeline(cached=args.cached, force=args.force, members_pull=args.members,
             deliverable=args.deliverable)


if __name__ == "__main__":
    main()