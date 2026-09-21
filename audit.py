#!/usr/bin/env python3
"""Soccer Wearhouse — product catalog audit.

Read-only: bulk-op fetch -> JSONL cache -> scan -> delta reports.
Never writes to the store. Deltas are applied by a human via the Mechanic tasks.

  python audit.py            full pipeline (fetch + scan + reports)
  python audit.py --cached   re-scan the last downloaded JSONL (no network)
  python audit.py --lint     task-config lint only (no catalog needed)
  python audit.py --pdf      also emit the merchant-facing PDF
  python audit.py --force    cancel an in-flight bulk query op and restart
  python audit.py --forecast NEW-rules.json --filter player [--cached]
                             how many current blanks a delta rule set would fill (no other passes)
"""
import argparse
import datetime
import json
import os
import sys
from collections import Counter

import config
import report
from catalog import FILTERS, PRODUCTS_BULK_QUERY, load_products
from passes import (FORECAST_ROW_FIELDS, blanks_reason_counts, pass_addon_coverage,
                    pass_filled_check, pass_filter_blanks, pass_fill_rates, pass_forecast,
                    pass_no_rule_candidates, pass_vendor_audit, EVIDENCE_ROW_FIELDS,
                    SURNAME_BUCKET_FIELDS, pass_entity_evidence, pass_surname_buckets)
from rules import FILTER_HANDLES, config_lint, load_task_rules
from subcat_sim import SIM_ROW_FIELDS, pass_subcat_sim
from shopify_client import ShopifyBulk, get_admin_token


def _log_lint(findings, log):
    for f in findings:
        log(f"  [{f['level']}] {f['area']}: {f['message']}")


def _fetch_products(force, log):
    client = ShopifyBulk(config.SHOP_DOMAIN, get_admin_token(log), config.API_VERSION)
    log("Kicking off products bulk operation...")
    client.run_to_file(PRODUCTS_BULK_QUERY, config.CACHE_PRODUCTS_JSONL, force=force, log=log)


def pipeline(cached=False, force=False, pdf=False, lint_only=False, lag_hours=None, log=print):
    cfg = load_task_rules(config.TASK_CONFIGS)
    if cfg:
        log(f"Task rules loaded from {config.TASK_CONFIGS} (dated {cfg['_meta']['file_date']}).")
    else:
        log("No task-configs.json — running in catalog-derived fallback mode "
            "(reason codes limited to CANDIDATE). Run make_task_configs.py to fix.")
    findings = config_lint(cfg)
    if lint_only:
        _log_lint(findings, log)
        return
    errors = [f for f in findings if f["level"] == "ERROR"]
    if errors:
        log(f"Config lint: {len(errors)} ERROR finding(s) — details in config-lint CSV.")

    if not cached:
        _fetch_products(force, log)

    if not os.path.exists(config.CACHE_PRODUCTS_JSONL):
        sys.exit(f"No cached JSONL at {config.CACHE_PRODUCTS_JSONL}. Run without --cached first.")

    log("Loading products...")
    products = load_products(config.CACHE_PRODUCTS_JSONL)
    log(f"  {len(products):,} products")

    stamp = config.now().strftime("%Y%m%d-%H%M")
    log("Running audits...")

    blanks = pass_filter_blanks(products, cfg, lag_hours=lag_hours)
    report.write_blank_csvs(stamp, blanks, log=log)
    for filter_key, counts in blanks_reason_counts(blanks).items():
        if counts:
            log(f"  {filter_key}: " + "  ".join(f"{r}={n}" for r, n in sorted(counts.items())))

    filled = pass_filled_check(products, cfg)
    if filled:
        report.write_filled_csvs(stamp, filled["rows"], log=log)
        for filter_key, rows in filled["rows"].items():
            if rows:
                counts = Counter(r["finding"] for r in rows)
                log(f"  {filter_key} filled-check: " + "  ".join(f"{f}={n}" for f, n in sorted(counts.items())))

    prev, prev_name = report.load_previous_summary()
    summary = pass_fill_rates(products, cfg, prev=prev, prev_name=prev_name)
    if filled:
        summary["filled_orphans"] = filled["orphans"]
    report.write_summary(stamp, summary, log=log)
    report.write_snapshot_md(summary, prev, config.CACHE_PRODUCTS_JSONL, log=log)
    log(f"  active={summary['active']:,}  "
        + "  ".join(f"{f.split('_')[0]}={summary[f'fill_{f}']['pct']}%" for f in FILTERS))
    if prev:
        moved = summary.get("delta_vs_previous", {})
        log(f"  diffed vs {prev_name}: {len(moved)} metric(s) moved")

    coverage = pass_addon_coverage(products, cfg)
    if coverage:
        if coverage["missing_tag"]:
            report.write_rows_csv(os.path.join(config.run_dir(stamp), f"addon-missing-tag-{stamp}.csv"),
                                  coverage["missing_tag"],
                                  ["handle", "title", "subcats", "admin_url", "storefront_url"])
        if coverage["stale_tag"]:
            report.write_rows_csv(os.path.join(config.run_dir(stamp), f"addon-stale-tag-{stamp}.csv"),
                                  coverage["stale_tag"],
                                  ["handle", "title", "subcats", "admin_url", "storefront_url"])
        log(f"  addon coverage: missing={len(coverage['missing_tag'])} (expect 0)  "
            f"stale={len(coverage['stale_tag'])}  "
            f"no-subcat(always-types)={sum(coverage['no_subcat_always_types'].values())}")
    else:
        log("  addon coverage skipped (no addon_eligible_subcats in config).")

    vendors = pass_vendor_audit(products, cfg)
    if vendors["rows"]:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"vendor-audit-{stamp}.csv"),
                              vendors["rows"], ["vendor", "count", "finding", "suggested"])
    log(f"  vendors: {vendors['distinct_vendors_active']} distinct, {len(vendors['rows'])} finding(s)")

    sim = pass_subcat_sim(products, cfg)
    if sim:
        if sim["rows"]:
            report.write_rows_csv(os.path.join(config.run_dir(stamp), f"subcat-sim-{stamp}.csv"),
                                  sim["rows"], SIM_ROW_FIELDS)
        log("  subcat sim: " + ("  ".join(f"{f}={n}" for f, n in sorted(sim["counts"].items())) or "clean"))
    else:
        log("  subcat sim skipped (no subcat map in config).")

    candidates = pass_no_rule_candidates(products, cfg)
    if candidates:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"no-rule-candidates-{stamp}.csv"),
                              candidates, ["token", "count", "sample_title"])
        log(f"  no-rule candidates (heuristic, human review): {len(candidates)} tokens")

    evidence = pass_entity_evidence(blanks, filled, candidates, cfg,
                                    collections_path=config.CACHE_COLLECTIONS_JSONL)
    if evidence:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"entity-evidence-{stamp}.csv"),
                              evidence, EVIDENCE_ROW_FIELDS)
        log(f"  entity evidence: {len(evidence)} candidate entities")
    buckets = pass_surname_buckets(blanks, products, cfg)
    if buckets:
        report.write_rows_csv(os.path.join(config.run_dir(stamp), f"surname-buckets-{stamp}.csv"),
                              buckets, SURNAME_BUCKET_FIELDS)
        log(f"  surname buckets: {len(set(r['surname'] for r in buckets))} surnames, {len(buckets)} context rows")

    attention = []
    sync_miss = sum(1 for rows in blanks.values() for r in rows if r["reason"] == "SYNC_MISS")
    if sync_miss:
        attention.append(f"SYNC_MISS={sync_miss}")
    invalid = sum(1 for rows in blanks.values() for r in rows if r["reason"] == "INVALID_CHOICE")
    if invalid:
        attention.append(f"INVALID_CHOICE={invalid}")
    if coverage and coverage["missing_tag"]:
        attention.append(f"addon_missing={len(coverage['missing_tag'])}")
    if errors:
        attention.append(f"lint_errors={len(errors)}")
    log("  HEALTH: " + ("ATTENTION — " + "  ".join(attention) if attention else "OK"))
    report.write_lint(stamp, findings, log=log)
    if pdf:
        report.build_pdf(stamp, summary, blanks, coverage, vendors, findings, log=log)
    log("Done.")


def forecast(rules_path, handle, cached=False, force=False, log=print):
    cfg = load_task_rules(config.TASK_CONFIGS)
    if not cfg or not (cfg.get(handle) or {}).get("_compiled"):
        sys.exit(f"Forecast needs the live '{handle}' rules in {config.TASK_CONFIGS} "
                 f"(run make_task_configs.py).")
    try:
        with open(rules_path, encoding="utf-8") as fh:
            new_rules = json.load(fh)
    except (OSError, ValueError) as e:
        sys.exit(f"Cannot read {rules_path}: {e}")
    if not (isinstance(new_rules, list) and new_rules and all(isinstance(r, dict) for r in new_rules)):
        sys.exit(f"{rules_path} must be a non-empty JSON list of "
                 f'{{"canonical_value", "safe_keywords"}} rules.')

    if not os.path.exists(config.CACHE_PRODUCTS_JSONL):
        if cached:
            sys.exit(f"No cached JSONL at {config.CACHE_PRODUCTS_JSONL}. Run without --cached first.")
        _fetch_products(force, log)
    cache_date = datetime.date.fromtimestamp(os.path.getmtime(config.CACHE_PRODUCTS_JSONL)).isoformat()
    log(f"Rules dated {cfg['_meta']['file_date']}, catalog cache dated {cache_date}.")

    out = pass_forecast(load_products(config.CACHE_PRODUCTS_JSONL), cfg, handle, new_rules)
    stamp = config.now().strftime("%Y%m%d-%H%M")
    path = report.write_rows_csv(os.path.join(config.run_dir(stamp), f"forecast-{handle}-{stamp}.csv"),
                                 out["rows"], FORECAST_ROW_FIELDS)
    log(f"  wrote {path}  ({len(out['rows'])} rows)")
    log(f"  {out['new_rules']} new rules would fill {out['blanks_filled']} of {out['blanks']} current blanks")
    flagged = sum(1 for r in out["rows"] if r["flags"])
    if flagged:
        log(f"  {flagged} rule(s) flagged; see the flags column.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cached", action="store_true", help="scan the last downloaded JSONL, skip fetch")
    ap.add_argument("--force", action="store_true", help="cancel any in-flight bulk query op and restart")
    ap.add_argument("--pdf", action="store_true", help="also emit the merchant-facing PDF")
    ap.add_argument("--lint", action="store_true", help="task-config lint only")
    ap.add_argument("--lag-hours", type=int, default=None, help="SYNC_LAG window (default 48)")
    ap.add_argument("--forecast", metavar="NEW-RULES.json",
                    help="forecast how many current blanks a delta rule set would fill (no other passes)")
    ap.add_argument("--filter", choices=[h for _, h in FILTER_HANDLES], help="filter for --forecast")
    args = ap.parse_args()
    if args.forecast:
        if not args.filter:
            ap.error("--forecast requires --filter")
        if args.lint or args.pdf:
            ap.error("--forecast runs no other passes; drop --lint/--pdf")
        return forecast(args.forecast, args.filter, cached=args.cached, force=args.force)
    if args.filter:
        ap.error("--filter is only used with --forecast")
    pipeline(cached=args.cached, force=args.force, pdf=args.pdf,
             lint_only=args.lint, lag_hours=args.lag_hours)


if __name__ == "__main__":
    main()
