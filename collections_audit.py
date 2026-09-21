#!/usr/bin/env python3
"""Soccer Wearhouse — collections health + automation-candidate audit.

Pinned to API 2026-07: Collection.sources / CollectionConditionsSource only
exist there. Read-only; suggested rules are applied by a human in admin.

  python collections_audit.py            fetch + audit + save baseline
  python collections_audit.py --cached   re-audit the last downloaded JSONL
  python collections_audit.py --force    cancel in-flight bulk query op first
"""
import argparse
import datetime
import json
import os
import re
import sys

import config
import report
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

KEEP_MANUAL = ("sale", "clearance", "black friday", "cyber", "gift", "new arrival",
               "best seller", "copa america", "euro", "world cup", "champions league")
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


def find_candidates(collections, cfg):
    compiled = {handle: ((cfg or {}).get(handle) or {}).get("_compiled")
                for _, handle in FILTER_HANDLES}
    rows = []
    for c in collections:
        if c["has_rule"] or c["count"] == 0:
            continue
        title_l = c["title"].lower()
        if any(k in title_l for k in KEEP_MANUAL):
            classification, suggested = "KEEP_MANUAL", ""
        elif c["title"] != normalize(c["title"]).title() and any(ch in c["title"] for ch in "áéíóúñüçàèö"):
            classification, suggested = "REVIEW", "accented title — verify canonical value manually"
        else:
            core = c["title"]
            for _ in range(3):
                stripped = _SUFFIX.sub("", core).strip()
                if stripped == core:
                    break
                core = stripped
            matches = []
            for handle, comp in compiled.items():
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
                classification = "AUTOMATABLE"
                suggested = f"custom.{handle}_filter equals '{value}' (additive OR rule; keep manual selections)"
            elif matches:
                classification, suggested = "REVIEW", "multiple/ambiguous entity matches"
            else:
                classification, suggested = "REVIEW", "no decomposition — likely thematic/custom"
        rows.append({"title": c["title"], "handle": c["handle"], "count": c["count"],
                     "classification": classification, "suggested_rule": suggested,
                     "admin_url": config.admin_collection_url(c["id"])})
    order = {"AUTOMATABLE": 0, "REVIEW": 1, "KEEP_MANUAL": 2}
    rows.sort(key=lambda r: (order[r["classification"]], -r["count"]))
    return rows


def pipeline(cached=False, force=False, log=print):
    cfg = load_task_rules(config.TASK_CONFIGS)
    if not cached:
        client = ShopifyBulk(config.SHOP_DOMAIN, get_admin_token(log), config.COLLECTIONS_API_VERSION)
        log(f"Kicking off collections bulk operation (API {config.COLLECTIONS_API_VERSION})...")
        client.run_to_file(COLLECTIONS_BULK_QUERY, config.CACHE_COLLECTIONS_JSONL, force=force, log=log)
    if not os.path.exists(config.CACHE_COLLECTIONS_JSONL):
        sys.exit(f"No cached JSONL at {config.CACHE_COLLECTIONS_JSONL}. Run without --cached first.")

    collections = load_collections(config.CACHE_COLLECTIONS_JSONL)
    log(f"  {len(collections)} collections ({sum(1 for c in collections if c['has_rule'])} with rules)")
    baseline = load_baseline()
    if not baseline:
        log("  no baseline yet — this run establishes it; diffs start next run.")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    health = health_check(collections, baseline, cfg)
    if health:
        report.write_rows_csv(os.path.join(config.OUT_DIR, f"collections-health-{stamp}.csv"),
                              health, ["title", "handle", "count", "finding", "detail", "admin_url"])
    log(f"  health findings: {len(health)}")

    candidates = find_candidates(collections, cfg)
    if candidates:
        report.write_rows_csv(os.path.join(config.OUT_DIR, f"collections-candidates-{stamp}.csv"),
                              candidates,
                              ["title", "handle", "count", "classification", "suggested_rule", "admin_url"])
    auto = sum(1 for r in candidates if r["classification"] == "AUTOMATABLE")
    log(f"  candidates: {len(candidates)} manual collections ({auto} look automatable)")

    if not cached or not baseline:
        save_baseline(collections, log=log)
    else:
        log("  baseline unchanged (cached re-scan).")
    log("Done.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cached", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    pipeline(cached=args.cached, force=args.force)


if __name__ == "__main__":
    main()
