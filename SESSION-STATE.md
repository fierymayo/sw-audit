# SESSION-STATE — sw-audit
Updated: 2026-09-30 (post keyword-batch-2). Read ROLE-AUDIT-DESIGN-CHAT.md first.

## Live Mechanic task configs (verified via export → make_task_configs → lint)
- player: **203 rules / 203 allowed values**, add_only. Batch 1 (+31 new, 16
  extended) verified live; batch 2 (+5: Luke Shaw, Matthijs de Ligt, Leny
  Yoro, Ayden Heaven, Patrick Chinazaekpere Dorgu; −1: Frenkie de Jong
  removed to fix a De Jong value split) pasted+saved, awaiting first sync.
- club: **88 rules / 88 allowed values** (87 + USMNT; Nashville/Charlotte/
  San Diego/Leon extended). Batch verified live: blanks 38 → 13.
- country 46, subcat 29 addon SubCats, normalize 34/30 — unchanged.
- Option keys renamed to `...json__code_multiline...` (code editor boxes);
  make_task_configs matches by prefix, unaffected.
- Lint: clean; 3 INFO (club 88/128 cap, subcat 29, normalize 34).

## Latest full audit — run 20260930-0039 + collections 0040 (all paths post-reorg)
- 22,026 products, active 18,758. Fill: club 64.9%, country 17.7%,
  player 25.7%, tournament 10.7%. HEALTH: OK (products side).
- player blanks 221 = NO_RULE_VALUE 205 (James 76, Rodrigo 67 — ambiguous,
  stay excluded; Shaw 35, de Ligt 23, Yoro 2, Heaven 2 — rostered by
  batch 2, will fill) + KEYWORD_GAP 16 (Felix Nmecha 7, Brandt Bronico 3,
  Henry Martín ~5, Merino-Arsenal 1 — deliberate exclusions/edge, no action).
- club blanks 13: 11 "Leon Goretzka"-class + Ramon Juarez + Andrey Santos.
  Audit-matcher heuristic noise ONLY — live task matches stricter and has
  never written these. No store risk. Do not re-litigate.
- **KEYWORD_GAP semantics (corrected): "audit heuristic thinks a keyword is
  missing", NOT "task will fill next sync".**
- player filled-check 76: 60 DIVERGENT (43 were De Jong→Frenkie split, fixed
  by batch-2 removal; 17 multi-player-title noise — posters/tees naming
  several players, no action) + 16 ORPHAN (5 legitimized by batch 2; rest =
  re-tag list below).
- club filled-check 38: 26 ORPHAN (No Club ×20 sentinel [parked decision],
  Elite Sport Institute ×4, Metro Stars, Mutiny) + 12 DIVERGENT = genuinely
  wrong hand-tags (e.g. Chivas jacket tagged Man City) — merchant fix list,
  see filled-check-club CSV of run 0039; predicted column is the fix.
- country 3 DIVERGENT: "USA vs X" tees — known, fine.
- Collections (fresh members pull, 345,569 memberships): ready=14,
  AUTOMATABLE=3, COLOUR_VERIFY=11, REVIEW=105. HEALTH: ATTENTION with
  COUNT_DROP=1 = Kobbie Mainoo 19→18 (benign product drop-out; baseline
  re-saved at 18). Other findings are the known set (4 ZERO_WITH_RULE dead
  collections, On Sale discount values ×4, Quiñones MULTI_SOURCE).

## Infrastructure shipped 2026-09-29/30 (all sandbox- or Cowork-verified)
- shopify_client.py: network resilience — _gql retry w/ backoff (429/5xx/
  net errors; 4xx fail fast), wait() survives poll outages, run_to_file
  resumes RUNNING / reuses COMPLETED bulk ops within 2h via
  output/caches/.bulkops.json, atomic .part downloads.
- output/ reorg: caches/ runs/ snapshots/ summaries/ paste/. Previous-run
  lookups follow. HOW-IT-WORKS §7 + README + CLAUDE.md updated. 56 tests OK.
- make_paste_lists.py: `--remove "<canonical>"` flag (repeatable) — removals
  now first-class in the pipeline.
- dashboard.py: tkinter launcher (Full Audit / Products / Collections /
  Lint / Stop / Open Output), subprocess streaming. Stop is safe: bulk op
  continues server-side and next run resumes it.
- ROLE-AUDIT-DESIGN-CHAT.md: role doc in repo + Project knowledge. Chat-
  switch ritual: "prepare handoff" → commit SESSION-STATE → new chat reads
  role doc → paste SESSION-STATE.
- Project knowledge note: the attached local folder is the THEME code, not
  this repo — audit code still travels via paste or Cowork.

## Immediate next steps
1. Commit (if not already): player export, task-configs.json,
   delta-player-2.json, make_paste_lists.py, SESSION-STATE.md.
2. After next 06:00 player sync: dashboard → Full Audit. Expect player
   blanks 221 → ~159, NO_RULE_VALUE 205 → ~143 (James+Rodrigo only),
   FILLED_ORPHAN 16 → ~11, divergents 60 → ~17 (noise class only).
3. Hand merchant the two fix lists:
   a) player re-tags (12): James→real name, Rodrigo→real name,
      Estêvão→Estevao, João Pedro→Joao Pedro, Andreas Christensen→
      Christensen, Jules Koundé→Kounde, Karim Adeyemi→Adeyemi, Anthony
      Gordon→Gordon, Diogo Dalot→Dalot, Noussair Mazraoui→Mazraoui,
      Lisandro Martinez→Martinez, Frenkie de Jong→De Jong.
   b) club corrections (12): stored→predicted per
      filled-check-club_filter-20260930-0039.csv.
4. Then: collections automation phase (not started). 3 AUTOMATABLE
   additive rules ready: Goalkeeper Gloves (whole-type), Everton FC,
   San Jose Earthquakes. Colour rules need merchant verify (Red rejected
   206/360 Predator substring).

## Parked / open
- No Club ×20 sentinel decision; 4 ZERO_WITH_RULE dead collections;
  Villareal collection-title typo; Quiñones MULTI_SOURCE; Charly "León"
  accent gap (1 product); Henry-Martín keyword gap (~5 Club América
  products, risky vs Henry Kessler — hand-tag preferred); notes-persistence;
  weekly scheduler bat; 14 unrostered clubs list in entity-evidence.
- Known noise classes (never alarm on these): Goretzka-class club
  KEYWORD_GAP, Felix/Brandt/Henry player KEYWORD_GAP, 17 multi-player-title
  divergents, subcat WILL_REPLACE 3 oscillators + UNPREDICTED_EXISTING 7 +
  MISSING_SUBCAT 1 Topps tin, On Sale UNKNOWN_RULE_VALUE ×4, country
  USA-vs-X divergents ×3.