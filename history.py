#!/usr/bin/env python3
"""Append-only run metrics history: one JSON line per audit run, tracked in git.

  python history.py --backfill   seed history/metrics.jsonl from existing
                                 fill-rate summaries (idempotent)

audit.py and collections_audit.py append their line at the end of each run.
Never rewritten; trend data for dashboards and chats.
"""
import glob
import json
import os
import sys
from collections import Counter

import config


def _existing_runs():
    runs = set()
    if os.path.exists(config.METRICS_JSONL):
        with open(config.METRICS_JSONL, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    runs.add((rec.get("kind"), rec.get("run")))
    return runs


def append(record):
    os.makedirs(config.HISTORY_DIR, exist_ok=True)
    record = {"schema": 1, "ts": config.now().isoformat(timespec="seconds"), **record}
    with open(config.METRICS_JSONL, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _fill_pct(summary):
    return {k[5:]: v.get("pct") for k, v in summary.items()
            if k.startswith("fill_") and isinstance(v, dict)}


def append_products(stamp, products, summary, blanks, filled, health, extra=None):
    from passes import blanks_reason_counts
    rec = {"kind": "products", "run": stamp, "health": health,
           "products": len(products), "active": summary.get("active"),
           "fill_pct": _fill_pct(summary),
           "blank_reasons": {fk: dict(c) for fk, c in blanks_reason_counts(blanks).items() if c}}
    if filled:
        rec["filled_findings"] = {fk: dict(Counter(r["finding"] for r in rows))
                                  for fk, rows in filled["rows"].items() if rows}
        rec["orphan_products"] = {fk: sum(c.values())
                                  for fk, c in filled["orphans"].items() if c}
    if extra:
        rec.update(extra)
    return append(rec)


def append_collections(stamp, collections, candidates_counts, health_findings, health, extra=None):
    rec = {"kind": "collections", "run": stamp, "health": health,
           "collections": len(collections),
           "with_rules": sum(1 for c in collections if c.get("has_rule")),
           "candidates": dict(candidates_counts),
           "health_findings": health_findings}
    if extra:
        rec.update(extra)
    return append(rec)


def backfill(log=print):
    files = sorted(glob.glob(os.path.join(config.summaries_dir(), "fill-rate-summary-*.json"))
                   + glob.glob(os.path.join(config.OUT_DIR, "fill-rate-summary-*.json")),
                   key=os.path.basename)
    have = _existing_runs()
    added = 0
    for path in files:
        stamp = os.path.basename(path)[len("fill-rate-summary-"):-len(".json")]
        if ("products", stamp) in have:
            continue
        with open(path, encoding="utf-8") as fh:
            summary = json.load(fh)
        rec = {"kind": "products", "run": stamp, "backfilled": True,
               "active": summary.get("active"), "fill_pct": _fill_pct(summary)}
        orphans = summary.get("filled_orphans") or {}
        if orphans:
            rec["orphan_products"] = {fk: sum(c.values()) for fk, c in orphans.items() if c}
        if summary.get("generated_at"):
            rec["ts"] = summary["generated_at"]
        append(rec)
        added += 1
    log(f"backfill: {added} line(s) added from {len(files)} summaries "
        f"-> {config.METRICS_JSONL}")
    return added


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        backfill()
    else:
        sys.exit(__doc__)
