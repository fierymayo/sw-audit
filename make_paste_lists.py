#!/usr/bin/env python3
"""Build paste-ready Mechanic task options from a forecast-approved delta.

  python make_paste_lists.py NEW-rules.json player [--cached] [--remove "<canonical>"]

Merges the delta into the current task rules, refuses on collisions, and
writes to output/paste/<filter>-<stamp>/:
  keyword_rules_json.txt / allowed_values_json.txt  full option values
  patch-summary.md                                  what changed + forecast
  orphan-normalize.csv                              Matrixify import for stored
                                                    variants of patched canonicals
  definition-choices-to-add.txt                     club/country only
Operator pastes into Mechanic; this never calls write APIs.
"""
import datetime
import json
import os
import sys

import config
from catalog import load_products
from matcher import compile_rules, match_title, normalize
from rules import load_task_rules, FILTER_HANDLES, CLUB_CHOICE_LIST_CAP


def _norm(s):
    return " ".join((s or "").lower().split())


def _exports_newer_than_config(cfg):
    gen = (cfg.get("_meta") or {}).get("generated_at")
    try:
        gen_ts = datetime.datetime.fromisoformat(gen).timestamp()
    except (TypeError, ValueError):
        return "task-configs.json has no parseable generated_at"
    exp_dir = "task-exports"
    if not os.path.isdir(exp_dir):
        return f"{exp_dir}/ missing — re-export the task first"
    newer = [f for f in os.listdir(exp_dir) if f.endswith(".json")
             and os.path.getmtime(os.path.join(exp_dir, f)) > gen_ts]
    if newer:
        return (f"task export(s) newer than task-configs.json: {', '.join(sorted(newer))} "
                f"— run make_task_configs.py first")
    return None


def _lint_delta(existing_compiled, delta_rules):
    problems = []
    existing_owners = {}
    for canonical, kw, _l in existing_compiled:
        existing_owners.setdefault(kw, set()).add(canonical)
    seen = {}
    for rule in delta_rules:
        canonical = (rule.get("canonical_value") or "").strip()
        for raw in rule.get("safe_keywords") or []:
            kw = normalize(raw)
            if not kw:
                continue
            others = existing_owners.get(kw, set()) - {canonical}
            if others:
                problems.append(f"COLLISION_EXISTING: '{raw}' ({canonical}) -> "
                                f"already owned by {', '.join(sorted(others))}")
            prev = seen.get(kw)
            if prev and prev != canonical:
                problems.append(f"DUPLICATE_IN_DELTA: '{raw}' in both {prev} and {canonical}")
            seen[kw] = canonical
    return problems


def _audit_ignores():
    try:
        with open(config.AUDIT_IGNORE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def build_patch(delta_path, handle, cached=False, remove=(), log=print):
    if handle not in {h for _, h in FILTER_HANDLES}:
        sys.exit(f"Unknown filter '{handle}'.")
    cfg = load_task_rules(config.TASK_CONFIGS)
    if not cfg or not (cfg.get(handle) or {}).get("keyword_rules"):
        sys.exit(f"No keyword_rules for '{handle}' in {config.TASK_CONFIGS}.")
    stale = _exports_newer_than_config(cfg)
    if stale:
        sys.exit(f"REFUSED (stale config): {stale}")

    entry = cfg[handle]
    rules = [dict(r) for r in entry["keyword_rules"]]
    with open(delta_path, encoding="utf-8") as fh:
        delta = json.load(fh)
    for d in delta:
        if "add_keywords" in d and "safe_keywords" not in d:
            d["safe_keywords"] = d["add_keywords"]

    problems = _lint_delta(entry["_compiled"], delta)
    if problems:
        log("REFUSED — fix the delta first:")
        for p in problems:
            log(f"  {p}")
        sys.exit(1)

    by_canonical = {_norm(r.get("canonical_value")): r for r in rules}
    n_before = len(rules)
    added, extended_names, added_keywords = [], [], 0
    for d in delta:
        key = _norm(d["canonical_value"])
        if key in by_canonical:
            target = by_canonical[key]
            have = {_norm(k) for k in target.get("safe_keywords") or []}
            new = [k for k in d["safe_keywords"] if _norm(k) not in have]
            if new:
                target["safe_keywords"] = list(target.get("safe_keywords") or []) + new
                extended_names.append(target["canonical_value"])
                added_keywords += len(new)
        else:
            rules.append({"canonical_value": d["canonical_value"],
                          "safe_keywords": list(d["safe_keywords"])})
            by_canonical[key] = rules[-1]
            added.append(d["canonical_value"])

    removed = []
    for name in remove:
        key = _norm(name)
        if key in by_canonical:
            rules = [r for r in rules if _norm(r.get("canonical_value")) != key]
            del by_canonical[key]
            removed.append(name)
        else:
            log(f"  remove: '{name}' not found — skipped")

    allowed_before = list(entry.get("allowed_values") or [])
    allowed = [v for v in allowed_before if _norm(v) not in {_norm(n) for n in removed}]
    have = {_norm(v) for v in allowed}
    allowed += [c for c in added if _norm(c) not in have]

    if handle in ("club", "country") and allowed:
        n = len(allowed)
        if n > CLUB_CHOICE_LIST_CAP:
            sys.exit(f"REFUSED: {handle} allowed_values would be {n}/{CLUB_CHOICE_LIST_CAP} "
                     f"— over the choice-list cap.")
        if n >= 100:
            log(f"  WARN: {handle} allowed_values at {n}/{CLUB_CHOICE_LIST_CAP} choice-list cap.")

    stamp = config.now().strftime("%Y%m%d-%H%M")
    out_dir = os.path.join(config.paste_dir(), f"{handle}-{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    def _write_json(name, obj):
        path = os.path.join(out_dir, name)
        text = json.dumps(obj, indent=2, ensure_ascii=False)
        json.loads(text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        log(f"wrote {path}")
        return path

    _write_json("keyword_rules_json.txt", rules)
    if allowed_before:
        _write_json("allowed_values_json.txt", allowed)

    products = load_products(config.CACHE_PRODUCTS_JSONL)
    filter_key = next(k for k, h in FILTER_HANDLES if h == handle)
    patched_compiled = compile_rules(rules)
    existing_compiled = entry["_compiled"]
    fills = {}
    for p in products:
        if p["status"] != "ACTIVE" or p[filter_key]:
            continue
        best, _amb = match_title(p["title"], patched_compiled)
        if best and best != match_title(p["title"], existing_compiled)[0]:
            fills[best] = fills.get(best, 0) + 1

    patched_canonicals = {(r.get("canonical_value") or "").strip() for r in rules}
    ok_values = {v.strip() for v in (_audit_ignores().get("filled_orphans_ok") or {})
                 .get(filter_key) or []}
    mf_col = f"Metafield: custom.{filter_key} [single_line_text_field]"
    orphan_rows = []
    for p in products:
        if p["status"] != "ACTIVE":
            continue
        stored = p[filter_key]
        if not stored or stored in patched_canonicals or stored in ok_values:
            continue
        set_to, _amb = match_title(stored, patched_compiled)
        if set_to:
            orphan_rows.append((p["handle"], stored, set_to))
    orphan_path = os.path.join(out_dir, "orphan-normalize.csv")
    with open(orphan_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"Handle,current_value,{mf_col}\n")
        for handle_, cur, new in sorted(orphan_rows):
            fh.write(f"{handle_},\"{cur}\",\"{new}\"\n")
    log(f"wrote {orphan_path}  ({len(orphan_rows)} row(s))")

    if handle in ("club", "country") and added:
        choices_path = os.path.join(out_dir, "definition-choices-to-add.txt")
        with open(choices_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(added) + "\n")
        log(f"wrote {choices_path}  ({len(added)} choice(s) for Settings > Custom data)")

    summary_path = os.path.join(out_dir, "patch-summary.md")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(f"# Patch summary — {handle} ({stamp})\n\n")
        fh.write(f"Rules: {n_before} -> {len(rules)} "
                 f"(+{len(added)} new, {len(extended_names)} extended "
                 f"with {added_keywords} keyword(s), -{len(removed)} removed)\n")
        if allowed_before:
            fh.write(f"Allowed values: {len(allowed_before)} -> {len(allowed)}\n")
        fh.write(f"\nForecast fills on current active blanks: {sum(fills.values())}\n\n")
        for name in added:
            fh.write(f"- NEW {name}: {fills.get(name, 0)} blank(s)\n")
        for name in extended_names:
            fh.write(f"- EXTENDED {name}: +{fills.get(name, 0)} blank(s)\n")
        for name in removed:
            fh.write(f"- REMOVED {name}\n")
        if orphan_rows:
            fh.write(f"\norphan-normalize.csv: {len(orphan_rows)} stored value(s) "
                     f"to align via Matrixify import\n")
    log(f"wrote {summary_path}")
    log(f"{handle}: {n_before} -> {len(rules)} rules"
        + (f", {len(allowed_before)} -> {len(allowed)} allowed values" if allowed_before else ""))
    return out_dir


if __name__ == "__main__":
    args = sys.argv[1:]
    remove, cached = [], False
    while "--remove" in args:
        i = args.index("--remove")
        if i + 1 >= len(args):
            sys.exit("--remove needs a canonical value")
        remove.append(args[i + 1])
        del args[i:i + 2]
    if "--cached" in args:
        cached = True
        args.remove("--cached")
    if len(args) != 2:
        sys.exit(__doc__)
    build_patch(args[0], args[1], cached=cached, remove=remove)
