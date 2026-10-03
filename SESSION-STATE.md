# SESSION-STATE — sw-audit
Updated: 2026-10-03 (post batch-4, reorg, history/runlog, departures engine).
Read ROLE-AUDIT-DESIGN-CHAT.md first — NOTE: that doc is NOT in the repo and
predates the mirror; code now travels by push + connector resync (chats read
the mirror directly); outputs/CSVs still travel by paste or Cowork. Rewrite
it at the next handoff.

## Live Mechanic sync tasks (verified 226/88/46/29/34)
- player 226 rules/values: batch-1 (+31/+16kw bare-surname buckets),
  batch-2 (+5, De Jong split fix), batch-3 (+11 incl. qualified-only
  Andrey Santos), batch-4 (+12: Collyer, Jack Fletcher, Tyler Fletcher,
  Lacey, JJ Gabriel, Tynan Thompson, Tah, Ito, Pavlovic (qualified — bare
  collides with Strahinja Pavlović/Milan), Lennart Karl (qualified),
  Cardozo, Assomo). club 88, country 46, subcat 29, normalize 34/30.
  All add_only.
- Forecast parity confirmed live 3x (club 25=25 exact; batch-2 78->0;
  batch-3 106->0). Batch-4 forecast 8 fills (Tah 4, Ito 3, Pavlovic 1,
  hand-checked correct players) — confirms at next 06:00 sync.
- Rule-change workflow: delta JSON -> audit.py --forecast (review fill rows
  for bare keywords) -> make_paste_lists.py / audit.py --build-patch
  (refuses on collision/duplicate/stale-export/choice-cap; cache >24h WARN)
  -> paste both .txt into the task -> re-export -> make_task_configs.py.

## Normalize pipeline (orphan repair — NEW)
- Orphans are filled-with-variant values; add_only rules never fix them.
  build_patch emits orphan-normalize.csv (review) + orphan-normalize.json
  ({handle, expected_current, set_to}) — the payload for the player-filter
  normalize Mechanic task (collections chat's lane; recommend the task
  skip-and-log when stored != expected_current). The operator never
  file-imports; all store writes go through Mechanic.
- Batch-4 normalize RUN LIVE 2026-10-03: 34/35 written via task
  204d9b6e-7c72-4f89-9f4d-ef4a85136134 (CAS by handle), 1 refused
  correctly: "Minjae " trailing space = the Ticket-5 loader bug, now fixed
  (p["_raw"] exact bytes + FILLED_WHITESPACE finding). The next build_patch
  emits the exact-bytes follow-up row.

## Collections state
- 81 automated (56 metafield tier + 25 tranche: 12 type, 11 tag, 2
  metafield). 772 ruled in store. Holds closed KEEP_MANUAL: Backpacks
  (type=Bags over-pulls: gymsacks/duffels; nav label says "Bags &
  Backpacks" — merchant call parked), Premium Match Balls (curated subset
  of Official; a Ball-Match rule would dissolve it), Collectibles
  (fallback-tag bucket). Colour tier 11 parked ('red' in 'Predator' trap).
  REVIEW 80.
- Validation (shipped): type/tag/metafield rule values checked each run —
  casefolded (JERSEYS and Sale were case mismatches, cleared);
  per-definition vocab for custom.<filter> values. Real finding standing:
  Fabian Johnson rule on dead tag player_fabian-johnson (also
  ZERO_WITH_RULE) — merchant decision.
- Departures engine (shipped, LIVE-UNEXERCISED): members cache kept as
  .prev; on COUNT_DROP the detail gains departed/joined + causes
  (deleted / still ACTIVE / rule-mismatch / other) + examples, plus
  collections-departures-<stamp>.csv (still_matches_rule from the
  collection's own conditions; ALL/ANY honored; unknown relations
  INCLUDES/CONTAINS/NOT_TAGGED_WITH -> blank, never guessed). Eyeball the
  first real output.
- Apply-task flag (pre-tranche guard, parked): its conflict check is
  same-class only — before applying rules onto already-ruled collections,
  add a cross-class matchType:ALL guard. All 2026-10 targets were rule-less.

## App features (all committed through 2026-10-03)
- Repo layout: configs/ (task-configs.json, audit-ignore.json), deltas/,
  docs/; .py flat at root. Mirror = GitHub connector; push + resync ritual.
- history/metrics.jsonl (schema 1, append-only, tracked): per-run products
  + collections lines — fill %, blank reasons, filled findings, provenance
  (cached, cache_pulled_at, members_pulled_at, task_configs_date),
  rule/allowed counts, distinct stored, zero-fill canonicals, subcat sim,
  addon coverage, vendor, health_by_code, ruled_by_kind, applied_members,
  memberships. Backfilled to 2026-09-21. python history.py --backfill.
- runlog.py: console-<stamp>.log per run dir + crash traceback + run
  separator; groundwork for the (parked) weekly scheduler.
- Dated baselines: summaries/baselines/, newest 15.
- FILLED_ACKNOWLEDGED via configs/audit-ignore.json (club sentinels:
  No Club, Elite Sport Institute, Metro Stars, Mutiny) = 26 steady.

## Expected next fresh products run (after 06:00 player sync)
- SYNC_MISS 0; the 8 batch-4 blanks filled; player fill % up from 27.0.
- FILLED_ORPHAN 70 -> ~17-18 (normalize wrote 34; residual = hand-list +
  "Minjae "). FILLED_WHITESPACE is its own count now (canonical values with
  stray whitespace, previously invisible). club: ACKNOWLEDGED=26,
  DIVERGENT=12 steady.
- If SYNC_MISS persists after a sync: investigate task runs, not rules.

## Merchant items (deliver together)
- Hand-list, 7 values / ~18 products (merchant names the player):
  James 1, Rodrigo 1, Santos 3, Brown 3, Bara 4 (club nickname in the
  player field — clear or move), Karl 3 (-> Lennart Karl?), Pavlović 3
  (bare stored; qualified keywords can't map it).
- Duplicate-handle checks before normalizing: ...harry-maguire-away...
  blue-1 stores Lisandro Martinez; ...kobbie-mainoo-away...blue stores
  Diogo Dalot — title decides.
- Tagging convention: use canonical spellings (Rashford, Stanisic) or
  every wave lands as orphans.
- 15 wrong stored values from earlier (Pulisic on USA teammates, Saka on
  Arsenal teammates, Valverde on Uruguay, Modric on Dest, Lozano on
  Guillermo Martínez -> clear) — normalize-task candidates.
- Club corrections: 12 FILLED_DIVERGENT per filled-check-club CSV.
- Fabian Johnson collection: retag products or retire the rule.

## Parked / known noise (do not re-litigate)
- Parked: colour tier; compound Title+Type payload (task-side conditions
  array needed); dashboard buttons (build-patch picker, history trend);
  weekly scheduler; ROLE doc rewrite; Backpacks merchant call;
  README.md:3 "Matrixify export loop" line (accurate, optional reword);
  Henry-Martín kw (~5, risky vs Kessler); Charly "León" accent (1);
  4 ZERO_WITH_RULE dead collections; Villareal title typo; Quiñones
  MULTI_SOURCE; On Sale UNKNOWN_RULE_VALUE x4 (variant metafields,
  documented ignore); notes-persistence.
- Noise classes: Goretzka-class club KEYWORD_GAP (13); Felix/Brandt/Henry/
  Merino player KEYWORD_GAP (16); ~17 multi-player-title divergents;
  subcat sim MISSING 1 / WILL_REPLACE ~1-3 / UNPREDICTED 8; country
  USA-vs-X x3; benign COUNT_DROP churn (departures engine now explains
  real ones when .prev exists).