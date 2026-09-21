#!/usr/bin/env python3
"""Soccer Wearhouse — product catalog audit.

Read-only: bulk-op fetch -> JSONL cache -> scan -> delta reports.
Never writes to the store. Deltas are applied by a human via the Mechanic tasks.

  python audit.py            full pipeline (fetch + scan + reports)
  python audit.py --cached   re-scan the last downloaded JSONL (no network)
  python audit.py --lint     task-config lint only (no catalog needed)
  python audit.py --pdf      also emit the merchant-facing PDF
  python audit.py --force    cancel an in-flight bulk query op and restart
"""
import argparse
import datetime
import os
import sys

import config
import report
from catalog import FILTERS, PRODUCTS_BULK_QUERY, load_products
from passes import (blanks_reason_counts, pass_addon_coverage, pass_filter_blanks,
                    pass_fill_rates, pass_no_rule_candidates, pass_vendor_audit)
from rules import config_lint, load_task_rules
from shopify_client import ShopifyBulk, get_admin_token


def _log_lint(findings, log):
    for f in findings:
        log(f"  [{f['level']}] {f['area']}: {f['message']}")


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
        client = ShopifyBulk(config.SHOP_DOMAIN, get_admin_token(log), config.API_VERSION)
        log("Kicking off products bulk operation...")
        client.run_to_file(PRODUCTS_BULK_QUERY, config.CACHE_PRODUCTS_JSONL, force=force, log=log)

    if not os.path.exists(config.CACHE_PRODUCTS_JSONL):
        sys.exit(f"No cached JSONL at {config.CACHE_PRODUCTS_JSONL}. Run without --cached first.")

    log("Loading products...")
    products = load_products(config.CACHE_PRODUCTS_JSONL)
    log(f"  {len(products):,} products")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    log("Running audits...")

    blanks = pass_filter_blanks(products, cfg, lag_hours=lag_hours)
    report.write_blank_csvs(stamp, blanks, log=log)
    for filter_key, counts in blanks_reason_counts(blanks).items():
        if counts:
            log(f"  {filter_key}: " + "  ".join(f"{r}={n}" for r, n in sorted(counts.items())))

    prev, prev_name = report.load_previous_summary()
    summary = pass_fill_rates(products, cfg, prev=prev, prev_name=prev_name)
    report.write_summary(stamp, summary, log=log)
    log(f"  active={summary['active']:,}  "
        + "  ".join(f"{f.split('_')[0]}={summary[f'fill_{f}']['pct']}%" for f in FILTERS))
    if prev:
        moved = summary.get("delta_vs_previous", {})
        log(f"  diffed vs {prev_name}: {len(moved)} metric(s) moved")

    coverage = pass_addon_coverage(products, cfg)
    if coverage:
        if coverage["missing_tag"]:
            report.write_rows_csv(os.path.join(config.OUT_DIR, f"addon-missing-tag-{stamp}.csv"),
                                  coverage["missing_tag"],
                                  ["handle", "title", "subcats", "admin_url", "storefront_url"])
        if coverage["stale_tag"]:
            report.write_rows_csv(os.path.join(config.OUT_DIR, f"addon-stale-tag-{stamp}.csv"),
                                  coverage["stale_tag"],
                                  ["handle", "title", "subcats", "admin_url", "storefront_url"])
        log(f"  addon coverage: missing={len(coverage['missing_tag'])} (expect 0)  "
            f"stale={len(coverage['stale_tag'])}  "
            f"no-subcat(always-types)={sum(coverage['no_subcat_always_types'].values())}")
    else:
        log("  addon coverage skipped (no addon_eligible_subcats in config).")

    vendors = pass_vendor_audit(products, cfg)
    if vendors["rows"]:
        report.write_rows_csv(os.path.join(config.OUT_DIR, f"vendor-audit-{stamp}.csv"),
                              vendors["rows"], ["vendor", "count", "finding", "suggested"])
    log(f"  vendors: {vendors['distinct_vendors_active']} distinct, {len(vendors['rows'])} finding(s)")

    candidates = pass_no_rule_candidates(products, cfg)
    if candidates:
        report.write_rows_csv(os.path.join(config.OUT_DIR, f"no-rule-candidates-{stamp}.csv"),
                              candidates, ["token", "count", "sample_title"])
        log(f"  no-rule candidates (heuristic, human review): {len(candidates)} tokens")

    report.write_lint(stamp, findings, log=log)
    if pdf:
        report.build_pdf(stamp, summary, blanks, coverage, vendors, findings, log=log)
    log("Done.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cached", action="store_true", help="scan the last downloaded JSONL, skip fetch")
    ap.add_argument("--force", action="store_true", help="cancel any in-flight bulk query op and restart")
    ap.add_argument("--pdf", action="store_true", help="also emit the merchant-facing PDF")
    ap.add_argument("--lint", action="store_true", help="task-config lint only")
    ap.add_argument("--lag-hours", type=int, default=None, help="SYNC_LAG window (default 48)")
    args = ap.parse_args()
    pipeline(cached=args.cached, force=args.force, pdf=args.pdf,
             lint_only=args.lint, lag_hours=args.lag_hours)


if __name__ == "__main__":
    main()
