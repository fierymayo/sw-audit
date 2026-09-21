# sw-audit — Soccer Wearhouse catalog audit

Read-only. Pulls the full catalog in one Shopify **bulk operation**, caches it as JSONL, scans it, and writes delta reports. Replaces the manual Matrixify export loop. It never writes to the store — you apply deltas yourself via the existing Mechanic tasks or admin.

## Setup

```
pip install -r requirements.txt        # python-dotenv (+ reportlab, only needed for --pdf)
cp .env.example .env                   # fill in SHOP_DOMAIN + CLIENT_ID + CLIENT_SECRET (ADMIN_TOKEN = optional override)
python make_task_configs.py            # after dropping Mechanic exports into task-exports/ (see below)
```

### Authentication (`read_products` only)

Dev Dashboard apps don't show a permanent `shpat_` token. Instead, put the app's
**Client ID + Client Secret** (App settings → Credentials) in `.env`. The tool then
mints a 24h admin token per run via the **client credentials grant** and caches it
in `output/.token.json`. Scopes come from the released app version.

If minting fails with `shop_not_permitted`, the app and store aren't in the same
Dev Dashboard organization. Then either:
- add `http://localhost:8737/callback` to the app version's Allowed redirection
  URL(s), release, and run `python get_token.py` once — it does the
  authorization-code flow in your browser and prints a permanent `ADMIN_TOKEN`
  for `.env`; or
- reuse an existing legacy admin custom app's `shpat_` token as `ADMIN_TOKEN`.

`ADMIN_TOKEN`, when set, always wins over Client ID/Secret. Credentials live only
in your `.env` and are never logged or printed.

## Commands

```
python audit.py                # full: bulk fetch + scan + reports
python audit.py --cached       # re-scan last download, no network
python audit.py --pdf          # also emit the merchant-facing PDF
python audit.py --lint         # task-config lint only
python audit.py --force        # cancel an in-flight bulk query op and restart
python collections_audit.py            # collections health + automation candidates
python collections_audit.py --cached
python ui.py                   # Tkinter front-end (same actions as buttons)
python -m unittest discover tests
```

## Keeping task-configs.json fresh

The audit's reason codes are only facts if its rules match the live Mechanic tasks. Refresh whenever a task's rules change:

1. In Mechanic, export each of the 5 tasks (club sync, country sync, player sync, SubCat/AgeGroup, Normalize) as JSON.
2. Drop the files into `task-exports/`.
3. `python make_task_configs.py` — verify the printed counts against the live tasks.

Every report prints the config file date; treat stale-dated runs with suspicion.

## Outputs (`output/`, timestamped)

| File | What it is |
|---|---|
| `blanks-<filter>-*.csv` | Active products blank on that filter, with a reason code, matched value, and admin/storefront links |
| `fill-rate-summary-*.json` | Job 1 snapshot (fill %, types, SubCat/addon counts) + delta vs the previous summary |
| `addon-missing-tag-*.csv` | Eligible-SubCat products missing `addon-eligible` (should be empty) |
| `addon-stale-tag-*.csv` | `addon-eligible` present but SubCat not in the 29-list (persists by design; review only) |
| `vendor-audit-*.csv` | NOT_NORMALIZED / CASING_ALIAS_CANDIDATE / NEW_VENDOR_CANDIDATE vendor findings |
| `no-rule-candidates-*.csv` | Heuristic frequent unknown title tokens — human review, never authoritative |
| `config-lint-*.csv` | Rule/allowed-value drift in the tasks themselves |
| `collections-health-*.csv` | RULE_LOST / COUNT_DROP / ZERO_WITH_RULE / MULTI_SOURCE / UNKNOWN_RULE_VALUE |
| `collections-candidates-*.csv` | Manual collections classified AUTOMATABLE / REVIEW / KEEP_MANUAL with a suggested additive rule |
| `audit-report-*.pdf` | Merchant-facing summary (`--pdf`, needs reportlab) |

## Reason codes (blanks CSVs)

| Code | Meaning | Action |
|---|---|---|
| SYNC_LAG | Task will fill it; product newer than the sync window (48h default) | Wait |
| SYNC_MISS | Task matcher hits but it's old and still blank | Investigate task runs |
| AMBIGUOUS | Equal-length keyword tie — task skips by design | Add a longer qualified keyword if worth it |
| INVALID_CHOICE | Matched canonical missing from allowed_values | Fix the task config (lint flags it too) |
| KEYWORD_GAP | Rostered entity in title, phrasing not covered by safe_keywords | Add a keyword if safe |
| NO_RULE_VALUE | Vocab value in title but no rule produces it | Decide: add a rule or leave manual |
| CANDIDATE | Fallback mode only (no task-configs.json) | Generate the config |

`SYNC_LAG` window is tunable: `--lag-hours` or `SYNC_LAG_HOURS` in `.env`.
