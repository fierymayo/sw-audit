#!/usr/bin/env python3
"""Merge a forecast-approved delta into the current task rules and write full
paste-ready lists for the Mechanic task options.

  python make_paste_lists.py delta-player.json player
  python make_paste_lists.py delta-club.json club
"""
import json
import os
import sys

import config


def norm(s):
    return " ".join((s or "").lower().split())


def main(delta_path, handle):
    with open(config.TASK_CONFIGS, encoding="utf-8") as fh:
        cfg = json.load(fh)
    entry = cfg.get(handle) or {}
    rules = [dict(r) for r in entry.get("keyword_rules") or []]
    if not rules:
        sys.exit(f"No keyword_rules for '{handle}' in {config.TASK_CONFIGS}.")
    with open(delta_path, encoding="utf-8") as fh:
        delta = json.load(fh)

    by_canonical = {norm(r.get("canonical_value")): r for r in rules}
    added_rules, extended, added_keywords = 0, 0, 0
    for d in delta:
        key = norm(d["canonical_value"])
        if key in by_canonical:
            target = by_canonical[key]
            have = {norm(k) for k in target.get("safe_keywords") or []}
            new = [k for k in d["safe_keywords"] if norm(k) not in have]
            if new:
                target["safe_keywords"] = list(target.get("safe_keywords") or []) + new
                extended += 1
                added_keywords += len(new)
        else:
            rules.append({"canonical_value": d["canonical_value"],
                          "safe_keywords": list(d["safe_keywords"])})
            by_canonical[key] = rules[-1]
            added_rules += 1

    out = os.path.join(config.paste_dir(), f"paste-{handle}-keyword-rules.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=2, ensure_ascii=False)
    print(f"wrote {out}  ({len(rules)} rules: +{added_rules} new, "
          f"{extended} extended with {added_keywords} keyword(s))")

    allowed = entry.get("allowed_values")
    if allowed:
        have = {norm(v) for v in allowed}
        merged = list(allowed) + [d["canonical_value"] for d in delta
                                  if norm(d["canonical_value"]) not in have]
        out = os.path.join(config.paste_dir(), f"paste-{handle}-allowed-values.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2, ensure_ascii=False)
        print(f"wrote {out}  ({len(merged)} values, +{len(merged) - len(allowed)} new)")
    else:
        print(f"'{handle}' has no allowed_values list — nothing extra to paste.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])