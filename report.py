import csv
import glob
import json
import os

import config
from passes import BLANK_ROW_FIELDS, blanks_reason_counts, blanks_top_values


def _stamped(name, stamp, ext):
    return os.path.join(config.OUT_DIR, f"{name}-{stamp}.{ext}")


def write_rows_csv(path, rows, fields):
    os.makedirs(config.OUT_DIR, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def write_blank_csvs(stamp, blanks, log=print):
    paths = []
    for filter_key, rows in blanks.items():
        if not rows:
            continue
        path = _stamped(f"blanks-{filter_key}", stamp, "csv")
        write_rows_csv(path, rows, BLANK_ROW_FIELDS)
        log(f"  wrote {path}  ({len(rows)} rows)")
        paths.append(path)
    return paths


def load_previous_summary():
    files = sorted(glob.glob(os.path.join(config.OUT_DIR, "fill-rate-summary-*.json")))
    if not files:
        return None, None
    with open(files[-1], encoding="utf-8") as fh:
        return json.load(fh), os.path.basename(files[-1])


def write_summary(stamp, summary, log=print):
    os.makedirs(config.OUT_DIR, exist_ok=True)
    path = _stamped("fill-rate-summary", stamp, "json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    log(f"  wrote {path}")
    return path


def write_lint(stamp, findings, log=print):
    if not findings:
        return None
    path = _stamped("config-lint", stamp, "csv")
    write_rows_csv(path, findings, ["level", "area", "message"])
    log(f"  wrote {path}  ({len(findings)} findings)")
    return path


def build_pdf(stamp, summary, blanks, coverage, vendors, lint, log=print):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        log("  PDF skipped: reportlab is not installed (pip install reportlab).")
        return None

    path = _stamped("audit-report", stamp, "pdf")
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    story = []

    def heading(text):
        story.append(Spacer(1, 10))
        story.append(Paragraph(text, styles["Heading2"]))

    def table(data, widths=None):
        t = Table(data, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f7")]),
        ]))
        story.append(t)

    story.append(Paragraph("Soccer Wearhouse — Catalog Audit", styles["Title"]))
    story.append(Paragraph(
        f"Generated {summary['generated_at']} &nbsp;·&nbsp; task rules file dated "
        f"<b>{summary.get('rules_file_date', 'NONE')}</b> (verify freshness before acting)", body))

    heading("Catalog snapshot")
    delta = summary.get("delta_vs_previous", {})

    def d(key):
        v = delta.get(key)
        if isinstance(v, dict):
            v = v.get("filled")
        return f"{v:+,}" if isinstance(v, (int, float)) and v else ""

    rows = [["Metric", "Value", "Δ vs previous"]]
    rows.append(["Total products", f"{summary['total']:,}", d("total")])
    rows.append(["Active products", f"{summary['active']:,}", d("active")])
    for f in ("club_filter", "country_filter", "player_filter", "tournament_filter"):
        s = summary[f"fill_{f}"]
        rows.append([f"{f} fill", f"{s['filled']:,} ({s['pct']}%)", d(f"fill_{f}")])
    rows.append(["SubCat-tagged", f"{summary['subcat_tagged_active']:,}", d("subcat_tagged_active")])
    rows.append(["addon-eligible", f"{summary['addon_eligible_active']:,}", d("addon_eligible_active")])
    table(rows, widths=[2.2 * inch, 2.0 * inch, 1.4 * inch])
    if summary.get("previous_run"):
        story.append(Paragraph(f"Diffed against {summary['previous_run']}", body))

    heading("Filter blanks by reason")
    reason_counts = blanks_reason_counts(blanks)
    reasons = sorted({r for counts in reason_counts.values() for r in counts})
    rows = [["Filter"] + reasons + ["Total"]]
    for filter_key, counts in reason_counts.items():
        rows.append([filter_key] + [str(counts.get(r, "")) for r in reasons] + [str(sum(counts.values()))])
    table(rows)
    story.append(Paragraph(
        "SYNC_LAG and SYNC_MISS are facts derived from the live task rules; "
        "KEYWORD_GAP and NO_RULE_VALUE need a rule/keyword decision; AMBIGUOUS is skipped by task design.", body))

    heading("Top blank values")
    for filter_key, top in blanks_top_values(blanks, top=8).items():
        if top:
            line = "  ·  ".join(f"{v} ×{n}" for v, n in top)
            story.append(Paragraph(f"<b>{filter_key}</b>: {line}", body))

    if coverage:
        heading("SubCat / add-on coverage")
        story.append(Paragraph(
            f"Eligible-SubCat products missing addon-eligible: <b>{len(coverage['missing_tag'])}</b> "
            f"(should be 0) · stale addon-eligible: <b>{len(coverage['stale_tag'])}</b> · "
            f"always-SubCat types with no SubCat: <b>{sum(coverage['no_subcat_always_types'].values())}</b>", body))

    if vendors:
        heading("Vendor audit")
        story.append(Paragraph(
            f"Distinct vendors on active: {vendors['distinct_vendors_active']} · "
            f"findings: {len(vendors['rows'])} (see vendor-audit CSV)", body))

    errors = [f for f in (lint or []) if f["level"] == "ERROR"]
    if errors:
        heading("Config lint errors")
        for f in errors[:12]:
            story.append(Paragraph(f"<b>{f['area']}</b>: {f['message']}", body))

    SimpleDocTemplate(path, pagesize=letter, title="SW Catalog Audit").build(story)
    log(f"  wrote {path}")
    return path
