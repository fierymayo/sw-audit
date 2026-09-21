#!/usr/bin/env python3
"""Build task-configs.json from Mechanic task JSON exports.

Refresh procedure: export each task from Mechanic (task page -> Export),
drop the .json files into task-exports/, run:  python make_task_configs.py
Counts are printed so you can verify against the live tasks before trusting a run.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

TASK_HANDLES = {
    "488c5217-2146-40a9-a7d5-046ef9ddaca0": "club",
    "6eafda0f-6e44-496e-bb27-d84344967a49": "country",
    "6af0e5de-4ae5-4163-9bed-cd120471b60a": "player",
    "32fa6df3-2648-48ca-a848-95c8a947757a": "subcat",
    "3d13ffef-2101-455b-a940-54eeb7f4f7d0": "normalize",
}
OUT_PATH = "task-configs.json"


def _opt(options, suffix):
    for key, value in options.items():
        if key.startswith(suffix):
            return value
    return None


def _json_opt(options, suffix):
    raw = _opt(options, suffix)
    if raw in (None, ""):
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def parse_filter_task(task):
    options = task.get("options", {})
    rules = _json_opt(options, "keyword_rules_json")
    if not rules:
        sys.exit(f"Task {task.get('id')}: no keyword_rules_json option found.")
    entry = {
        "task_id": task["id"],
        "task_name": task.get("name", ""),
        "write_mode": _opt(options, "write_mode") or "add_only",
        "keyword_rules": rules,
    }
    allowed = _json_opt(options, "allowed_values_json")
    if allowed:
        entry["allowed_values"] = allowed
    key = _opt(options, "metafield_key")
    if key:
        entry["metafield_key"] = key
    return entry


def parse_subcat_task(task):
    script = task.get("script", "")
    m = re.search(r'addon_eligible_subcats\s*=\s*"([^"]+)"', script)
    if not m:
        sys.exit(f"Task {task.get('id')}: addon_eligible_subcats not found in script.")
    options = task.get("options", {})
    return {
        "task_id": task["id"],
        "task_name": task.get("name", ""),
        "addon_eligible_subcats": [s.strip() for s in m.group(1).split(",") if s.strip()],
        "replace_incorrect_subcat_tags": bool(_opt(options, "replace_incorrect_subcat_tags")),
        "remove_addon_eligible_when_not_priority": bool(_opt(options, "remove_addon_eligible_when_not_priority")),
        "product_type_map": _opt(options, "product_type_map_old_value_to_canonical_value") or {},
    }


def parse_normalize_task(task):
    options = task.get("options", {})
    return {
        "task_id": task["id"],
        "task_name": task.get("name", ""),
        "vendor_map": _opt(options, "vendor_map_old_value_to_canonical_value") or {},
        "title_prefix_vendor_corrections": _opt(options, "title_prefix_vendor_corrections") or {},
        "vendor_brand_tags": _opt(options, "vendor_canonical_value_to_structured_brand_tag") or {},
        "product_type_map": _opt(options, "product_type_map_old_value_to_canonical_value") or {},
        "blank_type_values": _opt(options, "product_type_values_to_treat_as_blank") or [],
    }


def build(paths, out_path=OUT_PATH):
    cfg = {"_meta": {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                     "sources": []}}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            task = json.load(fh)
        handle = TASK_HANDLES.get(task.get("id", ""))
        if not handle:
            print(f"  skipped {os.path.basename(path)} — unknown task id {task.get('id')}")
            continue
        if handle in ("club", "country", "player"):
            cfg[handle] = parse_filter_task(task)
        elif handle == "subcat":
            cfg[handle] = parse_subcat_task(task)
        else:
            cfg[handle] = parse_normalize_task(task)
        cfg["_meta"]["sources"].append(os.path.basename(path))

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)

    print(f"Wrote {out_path}")
    for handle in ("club", "country", "player"):
        entry = cfg.get(handle)
        if entry:
            print(f"  {handle}: {len(entry['keyword_rules'])} rules, "
                  f"{len(entry.get('allowed_values', []))} allowed values, "
                  f"write_mode={entry['write_mode']}")
    if cfg.get("subcat"):
        print(f"  subcat: {len(cfg['subcat']['addon_eligible_subcats'])} addon-eligible SubCats")
    if cfg.get("normalize"):
        print(f"  normalize: {len(cfg['normalize']['vendor_map'])} vendor map entries, "
              f"{len(cfg['normalize']['title_prefix_vendor_corrections'])} title-prefix corrections")
    missing = [h for h in ("club", "country", "player", "subcat", "normalize") if h not in cfg]
    if missing:
        print(f"  WARNING — missing task exports for: {', '.join(missing)}")
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="task export files (default: task-exports/*.json|*.txt)")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()
    paths = args.paths or sorted(glob.glob("task-exports/*.json") + glob.glob("task-exports/*.txt"))
    if not paths:
        sys.exit("No exports found. Drop Mechanic task JSON exports into task-exports/ first.")
    build(paths, out_path=args.out)


if __name__ == "__main__":
    main()
