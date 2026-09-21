# HOW-IT-WORKS — sw-audit manual

Audience: the operator and AI sessions (design chat, Cowork runner/checker, Claude Code).
Contract and locked decisions live in CLAUDE.md — read that first, always. Current state
lives in SESSION-STATE.md. Report/reason-code definition tables live in README.md and are
NOT duplicated here; this manual explains how the machine works and how to operate it.

## 1. What this is — and never does

A read-only Python audit of the Soccer Wearhouse catalog (~21.9k products, ~18.6k active)
and its 910 collections. It pulls data via Shopify bulk operations, caches it as JSONL,
re-implements the Mechanic tasks' matching logic locally to *predict* what the tasks will
do, and writes reports that explain every blank, drift, and automation opportunity.

It NEVER writes to the store. No mutations exist anywhere in the codebase except
`bulkOperationCancel` on its own stuck query ops. Fixes are applied by a human through
the existing Mechanic tasks or Shopify admin. The app computes; humans (and the design
chat) decide. That division is a locked decision, not a current limitation.

## 2. The moving parts

```
.env ──► config.py ◄──────────────────────────────┐
                                                  │
task-exports/*.json ─► make_task_configs.py ─► task-configs.json ─► rules.py (load + lint)
                                                                        │ compiled rules
Shopify Admin GraphQL (2026-07)                                         ▼
  └─ bulk ops ─► shopify_client.py ─► output/catalog-*.jsonl ─► catalog.py / loaders
                                                                        │ product dicts
                       ┌────────────────────────────────────────────────┤
                       ▼                                                ▼
              audit.py (products pipeline)              collections_audit.py (collections pipeline)
                │ calls passes.py + subcat_sim.py         │ own engine + passes via evidence
                ▼                                          ▼
              report.py (CSV/JSON/MD/PDF writers)        deliverable.py (XLSX/PDF)
                       │                                        │
                       └────────► output/audit-<stamp>/ ◄───────┘
```

Module one-liners:

- **config.py** — env, paths, API versions, `run_dir(stamp)`, `now()` (UTC+5 stamps on
  every surface; override with `STAMP_UTC_OFFSET_HOURS`).
- **shopify_client.py** — token resolution + `ShopifyBulk`. Polls by its own op id via
  `bulkOperation(id:)`; `running()` sees only this app's ops; `--force` cancels only those.
- **catalog.py** — products bulk query + JSONL→dict loader (`__parentId` stitching for
  metafields).
- **rules.py** — loads task-configs.json, compiles keyword rules, lints config drift.
  `FILTER_HANDLES` = club/country/player only; tournament has no task and is never
  blank-audited.
- **matcher.py** — VERBATIM parity port of the tasks' normalize/compile/match. Locked.
- **subcat_sim.py** — VERBATIM port of the SubCat task's decision tree, live bugs
  included (pre-wrap asymmetry, substring semantics). Locked.
- **passes.py** — every products-side analysis (blanks with reason codes, filled-check,
  fill rates, forecast, addon coverage, vendor audit, no-rule tokens, entity evidence,
  surname buckets).
- **audit.py / collections_audit.py** — the two orchestrators (§5).
- **report.py / deliverable.py** — writers. `_stamped()` targets the run folder;
  snapshot MD and summary-chain logic handle root + run-folder history.
- **ui.py** — thin Tkinter front-end; buttons call the same pipelines. Basic actions
  only; forecast is CLI-only.
- **make_task_configs.py** — parses Mechanic task exports (matched by task UUID) into
  task-configs.json. The ONLY legitimate way that file changes.

## 3. Setup and authentication

```
pip install -r requirements.txt      # python-dotenv; reportlab for PDFs; openpyxl for XLSX
cp .env.example .env                 # SHOP_DOMAIN + credentials
python make_task_configs.py          # after dropping the 5 task exports into task-exports/
```

Token resolution order (get_admin_token):

1. `ADMIN_TOKEN` in .env — always wins. This is the current production setup (permanent
   token minted once via `python get_token.py` authorization-code flow).
2. Else `CLIENT_ID` + `CLIENT_SECRET` — mints a 24h token per day via the client
   credentials grant, cached at `output/.token.json`. Fails with `shop_not_permitted`
   when app and store are in different Dev Dashboard orgs (that's why we use #1).

Scope is `read_products` (plus harmless extra read scopes on the released app version).
The token is never logged, printed, or committed; `.env` and `output/` are gitignored.

## 4. Ground truth: task-configs.json

Every reason code is only a fact if this file matches the live Mechanic tasks. It is
GENERATED (task-exports/ → make_task_configs.py), never hand-edited. Verified counts at
generation time: player 168 rules, club 87, country 46, subcat 29 addon SubCats,
vendor_map 34. Every report prints the file's date (`generated_at`, falling back to
mtime) — treat stale-dated runs with suspicion. Without the file, audits run in
"fallback mode": catalog-derived phrases only, every finding becomes CANDIDATE, and
filled-check/forecast/sim are skipped.

## 5. Running it

Products (`audit.py`):

| Command | Network | What |
|---|---|---|
| `python audit.py` | 1 bulk op (~1–2 min) | fetch + all passes + reports |
| `python audit.py --cached` | none | re-scan last download |
| `python audit.py --pdf` | as above | + merchant PDF |
| `python audit.py --lint` | none | config lint only |
| `python audit.py --force` | — | cancel this app's stuck bulk op first |
| `python audit.py --lag-hours N` | — | SYNC_LAG window (default 48; also SYNC_LAG_HOURS in .env) |
| `python audit.py --forecast NEW.json --filter player [--cached]` | cache-dependent | rule-delta forecast ONLY (no other passes) |

Collections (`collections_audit.py`):

| Command | Network | What |
|---|---|---|
| `python collections_audit.py` | 1 bulk op (fast) | health + candidates + baseline |
| `python collections_audit.py --cached` | none | re-scan caches |
| `--members` | 2 sequential bulk ops (members walks ~343k memberships, several min) | + membership cache → real Adds / Count After |
| `--deliverable` | none extra | + XLSX/PDF; loads members cache if present, else Adds stay blank |

`python run_all.py` = fresh products (+PDF) then fresh collections (members +
deliverable). `python ui.py` = buttons for the common actions. Tests:
`python -m unittest discover tests` — the fixture WARNING about missing task exports is
normal (a generator test builds a player-only config).

Costs to remember: everything is read-only; a fresh products pull is one bulk query;
the members pull is the only slow one. Same-day caches are fine for iteration; for a
number that leaves the team (merchant XLSX), pull products and collections the same
morning so Adds math isn't computed across drifted caches.

## 6. Reading a run (console anatomy, products)

```
Task rules loaded from task-configs.json (dated 2026-09-21).   <- ground-truth freshness
Kicking off products bulk operation...
  bulk op started: gid://shopify/BulkOperation/...              <- OUR op id; polled by id only
  bulk op: RUNNING  objects=36322                               <- Shopify-side progress
  downloaded output/catalog-products.jsonl                      <- cache refreshed
  21,871 products
  wrote output/audit-<stamp>/blanks-club_filter-....csv  (38 rows)
  club_filter: KEYWORD_GAP=34  NO_RULE_VALUE=4                  <- reason mix per filter (README table)
  club_filter filled-check: FILLED_DIVERGENT=12  FILLED_ORPHAN=27
  active=18,600  club=64.5%  country=17.9%  player=23.6%  tournament=10.8%
  diffed vs fill-rate-summary-....json: 0 metric(s) moved       <- delta chain vs previous summary
  addon coverage: missing=0 (expect 0)  stale=4  no-subcat(always-types)=0
  vendors: 57 distinct, 11 finding(s)
  subcat sim: UNPREDICTED_EXISTING=6  WILL_REPLACE=5            <- forecast of Sunday's task run
  no-rule candidates (heuristic, human review): 40 tokens
  entity evidence: N candidate entities                          <- merged new-entity table
  surname buckets: N surnames, M context rows
```

`missing=0` on addon coverage is the standing health invariant. A non-empty
blanks-country line, SYNC_MISS anywhere, or addon missing>0 are the "look now" signals.

## 7. What lands on disk

```
output/
  catalog-products.jsonl              <- cache (overwritten each fetch)
  catalog-collections.jsonl           <- cache
  catalog-collections-members.jsonl   <- cache (only --members runs refresh it)
  collections-baseline.json           <- drift reference for RULE_LOST / COUNT_DROP
  catalog-snapshot-YYYY-MM-DD.md      <- doc chain, one per day, same-day reruns overwrite
  fill-rate-summary-*.json            <- may exist in root from pre-run-folder history
  .token.json                         <- only exists on CCG auth (not with ADMIN_TOKEN)
  audit-<stamp>/                      <- one folder per run (UTC+5 stamp), all CSVs/PDF/XLSX
```

Safe to delete: old `audit-*` folders (keep the latest), any loose stamped reports.
Keep: caches, baseline, the newest fill-rate-summary anywhere (it's the delta chain's
"previous" — `load_previous_summary` globs root AND run folders by basename), and the
snapshot MD chain. Deleting all summaries just resets deltas to first-run blanks.

## 8. Reading the reports

Definitions (columns, codes, meanings): README.md. What to DO with each:

- **blanks-<filter>** — the action list. SYNC_LAG = wait. SYNC_MISS = investigate task
  runs. KEYWORD_GAP / NO_RULE_VALUE = rule decisions (design chat). AMBIGUOUS = by
  design. INVALID_CHOICE = config bug (lint flags it too).
- **filled-check-<filter>** — informational, never a task to-fix list (tasks are
  add_only). Orphan value counts also land in the summary JSON and snapshot.
- **fill-rate-summary / catalog-snapshot** — the state of the world + delta vs previous.
  The snapshot is the diffable doc-chain artifact; provenance footer names rules date
  and cache.
- **subcat-sim** — what Sunday's task run will change. WILL_REPLACE = pending flips;
  UNPREDICTED_EXISTING = manual tags the task can't derive (preserved by design);
  MISSING_SUBCAT = will gain a tag.
- **entity-evidence** — ranked new-entity candidates. `products` counts affected
  products (no_rule_value + filled_orphan + corroborated tokens); `collection_count` is
  the size of a rule-less collection named after the entity. Product-backed rows rank
  first. Everything here is evidence for a human roster decision — nothing is
  auto-actionable.
- **surname-buckets** — for each KEYWORD_GAP surname: how blanks split by the product's
  club/country filter, with the top title words preceding the surname (first-name
  candidates). This is the dataset for the parked "qualified keywords vs star-only"
  design question.
- **vendor-audit / no-rule-candidates / config-lint** — hygiene lists; no-rule tokens
  are heuristic and never authoritative.
- **collections-health** — RULE_LOST and COUNT_DROP only fire against the baseline;
  ZERO_WITH_RULE = dead rules (merchant list); UNKNOWN_RULE_VALUE has a known false
  positive (§10).
- **collections-candidates + Collections-to-Automate XLSX** — tiers: AUTOMATABLE,
  COLOUR_VERIFY (always human-verified), REVIEW, KEEP_MANUAL (excluded from XLSX). All
  suggested rules are ADDITIVE. Adds/Count After only exist against a members cache.
  The Approve + Note columns are for the human review round.

## 9. Parity and trust

- `matcher.py` and `subcat_sim.py` are transcriptions of the live task code, bugs
  included, validated against the full catalog: 0 SYNC_MISS phantom findings, fill
  rates matching known state to the decimal, all 11 sim findings individually explained.
- Adds math was independently re-derived from raw caches (Everton cross-check + full
  Ready-sheet recount) — exact match.
- Colour rule evaluation uses plain substring on purpose = Shopify "Title contains"
  semantics; the audit's Adds mirror what the live rule would really do.
- Forecast and filled-check share the same matcher; synthetic tests pass, but neither
  has had a live confirmation event yet — the first applied forecast should be verified
  against actual task results.

## 10. Known limitations and false positives

- **Variant-metafield blind spot**: the products pull caches product-level custom
  metafields only. Collections ruled on variant metafields (e.g. discount_range's
  "% off" buckets on "On Sale") show UNKNOWN_RULE_VALUE — recognizable, ignore.
- **SYNC_LAG is a heuristic window** (48h default), not a task-log fact.
- **Leftover-word guard is conservative**: borderline-good matches (Bayer 04 Leverkusen)
  demote to REVIEW rather than risk a New-England-Revolution-class false rule.
- **Colour Adds inflate honestly**: 'red' substring-matches "Predator" (~206 of Red's
  360 adds) — that's what the Shopify rule would do; hence the verify tier.
- **Entity evidence** gates collection titles by word-shape + matcher + canonical
  containment; a handful of odd thematic titles can still slip through — it's a
  human-review table.
- **Whole-type threshold** uses member-and-type overlap when a members cache exists,
  falling back to raw counts without one (weaker; Misc-style mismatches possible).
- **Stamps are UTC+5 fixed** (no DST in Pakistan). Change STAMP_UTC_OFFSET_HOURS if
  that ever stops being true.

## 11. Roles and session workflow

- **Design chat (claude.ai)** — designs, writes verified code (sandbox-tested), reviews
  results, interprets. Sends exact Replace-this/With-this diffs or full files.
- **Cowork** — RUNNER/CHECKER on the local folder: runs commands, applies design-chat
  diffs verbatim, independently re-derives numbers from raw caches, reports exactly.
  Git access is READ-ONLY. If a diff's target text isn't in the file: stop and report,
  never guess.
- **Operator** — all git writes, all store-side actions, final judgment.
- Cold-session handshake: read CLAUDE.md, then SESSION-STATE.md, run the test suite,
  summarize, stop. SESSION-STATE.md is updated by the operator at session end.

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `shop_not_permitted` on token mint | CCG cross-org — use `python get_token.py` once, put ADMIN_TOKEN in .env |
| 401/403 on GraphQL | token revoked/rotated — re-mint via get_token.py |
| "A bulk query op is already in flight" | previous run still going or crashed mid-op — wait, or `--force` (cancels only this app's ops) |
| "No cached JSONL ... Run without --cached first" | cache missing — run a fetch |
| Adds column blank in XLSX | no members cache — run `--members` once (without `--cached`) |
| Tests print WARNING about missing task exports | normal — fixture builds a player-only config |
| Blank counts look frozen across runs | catalog genuinely unchanged (verify with updatedAt drift), or you re-scanned the same cache |
| Run folder sorts wrong vs others | pre-fix UTC-stamped folder from a non-local surface — delete it |