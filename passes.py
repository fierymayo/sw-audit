import datetime
import re
from collections import Counter, defaultdict

import config
from catalog import FILTERS
from matcher import compile_rules, match_title, normalize, phrase_in, strip_fc
from rules import FILTER_HANDLES

# Reason codes (facts, not prose):
#   SYNC_LAG        rule + keyword match, product newer than the lag window -> daily sync hasn't reached it yet
#   SYNC_MISS       rule + keyword match on an older product -> the sync should have filled it; investigate
#   AMBIGUOUS       task matcher hit an equal-length tie across canonicals -> skipped by design
#   INVALID_CHOICE  matched canonical is missing from allowed_values -> task skip_invalid_choice forever (config bug)
#   KEYWORD_GAP     a rostered canonical value appears in the title, but no safe_keyword matches this phrasing
#   NO_RULE_VALUE   a catalog vocab value appears in the title but no rule produces it (manual edits / removed rule)
#   CANDIDATE       fallback mode only (no task-configs.json): catalog-derived phrase hit
# Filled-check findings (filled values only; informational, never a to-fix list for the tasks):
#   FILLED_ORPHAN     stored value is not any rule canonical for that filter (rules/catalog drift)
#   FILLED_DIVERGENT  matcher predicts a different canonical than stored; info only — tasks are
#                     add_only and will never change a filled value

FALLBACK_GUARDS = {"williams", "lyon", "sunderland", "porto", "henry", "morgan",
                   "santos", "juarez", "araujo", "gordon"}
FALLBACK_MIN_LEN = 4

BLANK_ROW_FIELDS = ["handle", "title", "filter", "reason", "matched_value",
                    "created_at", "admin_url", "storefront_url"]
FILLED_ROW_FIELDS = ["handle", "title", "filter", "finding", "stored_value", "predicted_value",
                     "admin_url", "storefront_url"]
FORECAST_ROW_FIELDS = ["canonical", "keyword_count", "blanks_filled", "flags", "sample_title"]

ALWAYS_SUBCAT_TYPES = {"Air Freshener", "Enamel Pin", "Scarves", "Mini Figures", "Posters",
                       "Water Bottles", "Shin Guards", "Field Player Gloves", "Goalkeeper Gloves",
                       "Hats", "Socks", "Bags", "Balls", "Soccer Collectibles"}
CONDITIONAL_SUBCAT_TYPES = {"Accessories", "Decal / Sticker", "Footwear", "Shorts", "Pants",
                            "Compression", "Misc"}


def _active(products):
    return [p for p in products if p["status"] == "ACTIVE"]


def _blank_row(p, filter_key, reason, value):
    return {
        "handle": p["handle"], "title": p["title"], "filter": filter_key,
        "reason": reason, "matched_value": value or "",
        "created_at": p["created_at"].date().isoformat() if p.get("created_at") else "",
        "admin_url": config.admin_product_url(p["id"]),
        "storefront_url": config.storefront_product_url(p["handle"]),
    }


def pass_filter_blanks(products, cfg, lag_hours=None, now=None):
    lag_hours = lag_hours or config.SYNC_LAG_HOURS
    now = now or datetime.datetime.now(datetime.timezone.utc)
    active = _active(products)
    results = {}
    for filter_key, handle in FILTER_HANDLES:
        entry = (cfg or {}).get(handle)
        compiled = entry.get("_compiled") if entry else None
        allowed = entry.get("_allowed") if entry else set()
        canonicals = entry.get("_canonicals") if entry else set()

        vocab = {p[filter_key] for p in active if p[filter_key]}
        scan = []
        for v in sorted((canonicals or set()) | vocab):
            nv = normalize(v)
            if filter_key == "club_filter":
                nv = strip_fc(nv)
            if not nv:
                continue
            if not compiled and (len(nv) < FALLBACK_MIN_LEN or nv in FALLBACK_GUARDS):
                continue
            scan.append((v, nv, v in (canonicals or set())))
        scan.sort(key=lambda item: -len(item[1]))

        rows = []
        for p in active:
            if p[filter_key]:
                continue
            padded = " " + normalize(p["title"]) + " "
            reason, value = None, None
            if compiled:
                best, ambiguous = match_title(p["title"], compiled)
                if ambiguous:
                    reason = "AMBIGUOUS"
                elif best:
                    value = best
                    if allowed and best not in allowed:
                        reason = "INVALID_CHOICE"
                    elif p.get("created_at") and (now - p["created_at"]).total_seconds() <= lag_hours * 3600:
                        reason = "SYNC_LAG"
                    else:
                        reason = "SYNC_MISS"
                else:
                    for orig, nv, rostered in scan:
                        if phrase_in(padded, nv):
                            value = orig
                            reason = "KEYWORD_GAP" if rostered else "NO_RULE_VALUE"
                            break
            else:
                for orig, nv, _rostered in scan:
                    if phrase_in(padded, nv):
                        value, reason = orig, "CANDIDATE"
                        break
            if reason:
                rows.append(_blank_row(p, filter_key, reason, value))
        results[filter_key] = rows
    return results


def blanks_reason_counts(blanks):
    out = {}
    for filter_key, rows in blanks.items():
        out[filter_key] = dict(Counter(r["reason"] for r in rows))
    return out


def blanks_top_values(blanks, top=12):
    out = {}
    for filter_key, rows in blanks.items():
        out[filter_key] = Counter(r["matched_value"] for r in rows if r["matched_value"]).most_common(top)
    return out


def pass_filled_check(products, cfg):
    """Filled-value cross-check. None in fallback mode (no rules). One row per
    product/filter; an orphan that also has a prediction stays FILLED_ORPHAN."""
    active = _active(products)
    rows_by_filter, orphans = {}, {}
    for filter_key, handle in FILTER_HANDLES:
        entry = (cfg or {}).get(handle)
        compiled = entry.get("_compiled") if entry else None
        if not compiled:
            continue
        canonicals = entry["_canonicals"]
        rows, counts = [], Counter()
        for p in active:
            stored = p[filter_key]
            if not stored:
                continue
            predicted, _ambiguous = match_title(p["title"], compiled)
            if stored not in canonicals:
                finding = "FILLED_ORPHAN"
                counts[stored] += 1
            elif predicted and predicted != stored:
                finding = "FILLED_DIVERGENT"
            else:
                continue
            rows.append({
                "handle": p["handle"], "title": p["title"], "filter": filter_key,
                "finding": finding, "stored_value": stored, "predicted_value": predicted or "",
                "admin_url": config.admin_product_url(p["id"]),
                "storefront_url": config.storefront_product_url(p["handle"]),
            })
        rows_by_filter[filter_key] = rows
        orphans[filter_key] = dict(counts.most_common())
    if not rows_by_filter:
        return None
    return {"rows": rows_by_filter, "orphans": orphans}


def pass_forecast(products, cfg, handle, new_rules):
    """Blanks a delta rule set would fill, matched against existing + new rules
    together (longest wins, so an existing longer keyword still steals). None if
    the live rules for `handle` are not loaded."""
    entry = (cfg or {}).get(handle)
    existing = entry.get("_compiled") if entry else None
    if not existing:
        return None
    filter_key = next(k for k, h in FILTER_HANDLES if h == handle)
    delta = compile_rules(new_rules)

    stats = {}
    for rule in new_rules:
        canonical = (rule.get("canonical_value") or "").strip()
        if canonical:
            stats.setdefault(canonical, {"keyword_count": 0, "blanks_filled": 0,
                                         "flags": [], "sample_title": ""})
    existing_owners = defaultdict(set)
    for canonical, kw, _len in existing:
        existing_owners[kw].add(canonical)
    delta_owners = defaultdict(list)
    for canonical, kw, _len in delta:
        stats[canonical]["keyword_count"] += 1
        delta_owners[kw].append(canonical)
    for kw, owners in delta_owners.items():
        for canonical in sorted(set(owners)):
            for other in sorted(existing_owners.get(kw, set()) - {canonical}):
                stats[canonical]["flags"].append(f"COLLISION_EXISTING({kw} -> {other})")
            if len(owners) > 1:
                others = sorted(set(owners) - {canonical})
                stats[canonical]["flags"].append(
                    f"DUPLICATE_IN_DELTA({kw}" + (f" also in {', '.join(others)})" if others else ")"))

    merged = existing + delta
    blanks = [p for p in _active(products) if not p[filter_key]]
    filled = 0
    for p in blanks:
        best, _ambiguous = match_title(p["title"], merged)
        if best in stats and best != match_title(p["title"], existing)[0]:
            s = stats[best]
            s["blanks_filled"] += 1
            s["sample_title"] = s["sample_title"] or p["title"]
            filled += 1
    rows = [{"canonical": c, "keyword_count": s["keyword_count"], "blanks_filled": s["blanks_filled"],
             "flags": "|".join(s["flags"]), "sample_title": s["sample_title"]}
            for c, s in stats.items()]
    rows.sort(key=lambda r: (-r["blanks_filled"], r["canonical"]))
    return {"rows": rows, "new_rules": len(rows), "blanks_filled": filled, "blanks": len(blanks)}


def pass_fill_rates(products, cfg=None, prev=None, prev_name=None):
    total = len(products)
    active = _active(products)
    a = len(active)
    blankish = {"unclassified", "misc"}
    nrm = (cfg or {}).get("normalize") or {}
    if nrm.get("blank_type_values"):
        blankish = {v.strip().lower() for v in nrm["blank_type_values"]}

    summary = {
        "generated_at": config.now().isoformat(timespec="seconds"),
        "rules_file_date": ((cfg or {}).get("_meta") or {}).get("file_date", "NONE"),
        "total": total,
        "active": a,
        "status_breakdown": dict(Counter(p["status"] for p in products)),
    }

    def fill(count):
        return {"filled": count, "pct": round(100 * count / a, 1) if a else 0}

    for f in FILTERS:
        summary[f"fill_{f}"] = fill(sum(1 for p in active if p[f]))
    for key in ("product_age", "sibling_products"):
        summary[f"fill_{key}"] = fill(sum(1 for p in active if (p["_mf"].get(key) or "").strip()))

    grouped = [p for p in active if any(t.startswith("group_") for t in p["_tags_lc"])]
    members = defaultdict(set)
    for p in grouped:
        for t in p["_tags_lc"]:
            if t.startswith("group_"):
                members[t].add(p["gid"])
    multi = {t for t, m in members.items() if len(m) >= 2}
    in_multi = [p for p in grouped if any(t in multi for t in p["_tags_lc"])]
    summary["sibling_scope"] = {
        "products_with_group_tag": len(grouped),
        "multi_member_groups": len(multi),
        "products_in_multi_member_groups": len(in_multi),
        "filled_in_multi_member_groups": sum(1 for p in in_multi if (p["_mf"].get("sibling_products") or "").strip()),
    }

    summary["type_top12"] = Counter(p["type"] for p in active).most_common(12)
    summary["blank_type_active"] = sum(1 for p in active if not p["type"])
    for t in sorted(blankish):
        summary[f"type_{t}_active"] = sum(1 for p in active if p["type"].strip().lower() == t)
    summary["subcat_tagged_active"] = sum(1 for p in active if any(t.startswith("subcat_") for t in p["_tags_lc"]))
    summary["addon_eligible_active"] = sum(1 for p in active if "addon-eligible" in p["_tags_lc"])

    for f in FILTERS:
        summary[f"distinct_{f}"] = len({p[f] for p in active if p[f]})

    if prev:
        summary["previous_run"] = prev_name or ""
        summary["delta_vs_previous"] = _diff_summaries(prev, summary)
    return summary


def _diff_summaries(prev, cur):
    delta = {}
    for k, v in cur.items():
        pv = prev.get(k)
        if isinstance(v, dict) and isinstance(pv, dict) and "filled" in v and "filled" in pv:
            df, dp = v["filled"] - pv["filled"], round(v["pct"] - pv["pct"], 1)
            if df or dp:
                delta[k] = {"filled": df, "pct": dp}
        elif isinstance(v, (int, float)) and isinstance(pv, (int, float)) and not isinstance(v, bool):
            d = v - pv
            if d:
                delta[k] = d
    return delta


def pass_addon_coverage(products, cfg):
    subcat_cfg = (cfg or {}).get("subcat") or {}
    eligible = {s.strip().lower() for s in subcat_cfg.get("addon_eligible_subcats") or [] if s.strip()}
    if not eligible:
        return None
    active = _active(products)
    missing_tag, stale_tag = [], []
    no_subcat_always, no_subcat_conditional = Counter(), Counter()
    for p in active:
        subcats = [t for t in p["_tags_lc"] if t.startswith("subcat_")]
        has_addon = "addon-eligible" in p["_tags_lc"]
        has_eligible = any(s in eligible for s in subcats)
        if has_eligible and not has_addon:
            missing_tag.append({
                "handle": p["handle"], "title": p["title"],
                "subcats": "|".join(subcats),
                "admin_url": config.admin_product_url(p["id"]),
                "storefront_url": config.storefront_product_url(p["handle"]),
            })
        if has_addon and subcats and not has_eligible:
            stale_tag.append({
                "handle": p["handle"], "title": p["title"],
                "subcats": "|".join(subcats),
                "admin_url": config.admin_product_url(p["id"]),
                "storefront_url": config.storefront_product_url(p["handle"]),
            })
        if not subcats:
            if p["type"] in ALWAYS_SUBCAT_TYPES:
                no_subcat_always[p["type"]] += 1
            elif p["type"] in CONDITIONAL_SUBCAT_TYPES:
                no_subcat_conditional[p["type"]] += 1
    return {
        "eligible_count": len(eligible),
        "missing_tag": missing_tag,
        "stale_tag": stale_tag,
        "no_subcat_always_types": dict(no_subcat_always.most_common()),
        "no_subcat_conditional_types_info": dict(no_subcat_conditional.most_common()),
    }


def _squash(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def pass_vendor_audit(products, cfg):
    active = _active(products)
    counts = Counter(p["vendor"] for p in active if p["vendor"])
    nrm = (cfg or {}).get("normalize") or {}
    vmap = {k.strip().lower(): v.strip() for k, v in (nrm.get("vendor_map") or {}).items()}
    canonicals = set(vmap.values())
    rows = []
    if vmap:
        squash_to_canonical = {_squash(c): c for c in canonicals}
        for vendor, n in counts.most_common():
            if vendor in canonicals:
                continue
            key = vendor.lower()
            if key in vmap:
                rows.append({"vendor": vendor, "count": n, "finding": "NOT_NORMALIZED",
                             "suggested": vmap[key]})
            elif _squash(vendor) in squash_to_canonical:
                rows.append({"vendor": vendor, "count": n, "finding": "CASING_ALIAS_CANDIDATE",
                             "suggested": squash_to_canonical[_squash(vendor)]})
            elif n >= 2:
                rows.append({"vendor": vendor, "count": n, "finding": "NEW_VENDOR_CANDIDATE",
                             "suggested": ""})
    else:
        groups = defaultdict(list)
        for vendor, n in counts.items():
            groups[_squash(vendor)].append((vendor, n))
        for spellings in groups.values():
            if len(spellings) > 1:
                spellings.sort(key=lambda x: -x[1])
                canonical_guess = spellings[0][0]
                for vendor, n in spellings[1:]:
                    rows.append({"vendor": vendor, "count": n, "finding": "CASING_ALIAS_CANDIDATE",
                                 "suggested": canonical_guess})
    return {"distinct_vendors_active": len(counts), "rows": rows}


_TOKEN_STOP = {"home", "away", "third", "jersey", "jerseys", "soccer", "youth", "mens", "womens",
               "authentic", "replica", "training", "match", "long", "sleeve", "shirt", "kit",
               "socks", "cleats", "shorts", "pants", "jacket", "hoodie", "scarf", "goalkeeper",
               "gloves", "black", "white", "blue", "navy", "green", "yellow", "orange", "purple",
               "grey", "gray", "pink", "gold", "silver", "anniversary", "edition", "season"}


def pass_no_rule_candidates(products, cfg, top=40):
    """Heuristic only — frequent capitalized title tokens matching nothing known.
    Human review required; never treated as complete."""
    active = _active(products)
    known = set(_TOKEN_STOP)
    for _, handle in FILTER_HANDLES:
        entry = (cfg or {}).get(handle)
        for _, kw, _len in (entry.get("_compiled") or []) if entry else []:
            known.update(kw.split(" "))
    for p in active:
        if p["vendor"]:
            known.update(normalize(p["vendor"]).split(" "))
        for f in FILTERS:
            if p[f]:
                known.update(normalize(p[f]).split(" "))
    hits = Counter()
    samples = {}
    for p in active:
        if p["player_filter"] or p["club_filter"] or p["country_filter"]:
            continue
        for raw in p["title"].split(" "):
            token = raw.strip("()[],.&/-")
            if len(token) < 4 or not token.isalpha() or not token[0].isupper():
                continue
            key = normalize(token)
            if not key or key in known:
                continue
            hits[key] += 1
            samples.setdefault(key, p["title"])
    return [{"token": t, "count": n, "sample_title": samples[t]} for t, n in hits.most_common(top)]
