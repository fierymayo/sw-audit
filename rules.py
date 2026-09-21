import datetime
import json
import os
from collections import defaultdict

from matcher import compile_rules, normalize

FILTER_HANDLES = (("club_filter", "club"), ("country_filter", "country"), ("player_filter", "player"))
CLUB_CHOICE_LIST_CAP = 128


def load_task_rules(path):
    """Single loading point for task ground truth. Reads task-configs.json today;
    swapping this to a Mechanic cache endpoint later must not touch audit logic."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.setdefault("_meta", {})
    try:
        file_date = datetime.datetime.fromisoformat(cfg["_meta"].get("generated_at")).date().isoformat()
    except (TypeError, ValueError):
        file_date = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    cfg["_meta"]["file_date"] = file_date
    for handle in ("club", "country", "player", "tournament"):
        entry = cfg.get(handle)
        if entry and entry.get("keyword_rules"):
            entry["_compiled"] = compile_rules(entry["keyword_rules"])
            entry["_allowed"] = set(entry.get("allowed_values") or [])
            entry["_canonicals"] = {(r.get("canonical_value") or "").strip()
                                    for r in entry["keyword_rules"] if r.get("canonical_value")}
    return cfg


def config_lint(cfg):
    findings = []
    if not cfg:
        return [{"level": "WARN", "area": "config",
                 "message": "No task-configs.json found — audits run in catalog-derived fallback mode."}]

    def add(level, area, message):
        findings.append({"level": level, "area": area, "message": message})

    for _, handle in FILTER_HANDLES:
        entry = cfg.get(handle)
        if not entry:
            add("WARN", handle, "No entry in task-configs.json.")
            continue
        canonicals = entry.get("_canonicals", set())
        allowed = entry.get("_allowed", set())
        if allowed:
            missing = sorted(canonicals - allowed)
            for value in missing:
                add("ERROR", handle,
                    f"Rule canonical '{value}' is not in allowed_values — the task will "
                    f"skip_invalid_choice on it forever.")
            dead = sorted(allowed - canonicals)
            if dead:
                add("INFO", handle, f"{len(dead)} allowed value(s) no rule can produce: {', '.join(dead[:8])}"
                                    + ("…" if len(dead) > 8 else ""))
        kw_owners = defaultdict(set)
        for rule in entry.get("keyword_rules") or []:
            canonical = (rule.get("canonical_value") or "").strip()
            usable = [normalize(k) for k in rule.get("safe_keywords") or []]
            usable = [k for k in usable if k]
            if canonical and not usable:
                add("WARN", handle, f"Rule '{canonical}' has no usable safe_keywords.")
            for k in usable:
                kw_owners[k].add(canonical)
        for kw, owners in sorted(kw_owners.items()):
            if len(owners) > 1:
                add("ERROR", handle,
                    f"Keyword '{kw}' belongs to multiple canonicals ({', '.join(sorted(owners))}) — "
                    f"every match is permanently ambiguous.")
        if handle == "club" and allowed:
            n = len(allowed)
            level = "ERROR" if n >= 120 else "WARN" if n >= 110 else "INFO"
            add(level, handle, f"club allowed_values at {n}/{CLUB_CHOICE_LIST_CAP} choice-list cap"
                               + (" — drop the definition validation soon." if n >= 110 else "."))

    subcat = cfg.get("subcat") or {}
    eligible = subcat.get("addon_eligible_subcats") or []
    if eligible:
        add("INFO", "subcat", f"{len(eligible)} addon-eligible SubCats loaded.")
    else:
        add("WARN", "subcat", "No addon_eligible_subcats — add-on coverage pass will be skipped.")

    nrm = cfg.get("normalize") or {}
    vmap = nrm.get("vendor_map") or {}
    if vmap:
        add("INFO", "normalize", f"{len(vmap)} vendor map entries loaded.")
    else:
        add("WARN", "normalize", "No vendor_map — vendor audit runs in fallback (alias-candidates) mode.")
    return findings
