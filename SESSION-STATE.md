# SESSION-STATE — sw-audit
Updated: 2026-10-01 (tickets 1+3 shipped; payload contract fixed). Read
ROLE-AUDIT-DESIGN-CHAT.md first. NOTE for the role doc: the repo is now a
connected GitHub mirror in Project knowledge — chats read current code after
each push+resync; "code travels by paste" is obsolete, outputs/CSVs still
travel by upload or Cowork.

## Live Mechanic sync tasks (verified 203/88/46/29/34)
- player 203 rules/values (batch 1 +31/+16kw, batch 2 +5: Luke Shaw,
  Matthijs de Ligt, Leny Yoro, Ayden Heaven, Patrick Chinazaekpere Dorgu;
  Frenkie de Jong removed = De Jong split fix). club 88 (+USMNT, Leon 8kw).
  country 46, subcat 29, normalize 34/30. All add_only.
- Forecast parity CONFIRMED live: club batch-1 forecast 25 → actual 25
  exact; player 261 actives, residual accounted. Batch-2 forecast 78.

## Collections state
- Metafield tier DONE by the collections chat: 56 collections (37 club,
  16 country, 3 player) on additive rules via Mechanic task 07a010f6,
  double-verified. Their new apply-task consumes
  condition_spec {kind:"type"|"tag"|"metafield", value, [namespace,key]}.
- Audit now classifies tiers 1+2: AUTOMATABLE=28 (13 whole_type, 13 subcat,
  2 metafield: Everton, San Jose), COLOUR_VERIFY=11, REVIEW=80. Payload
  file collection-rules-payload-<stamp>.json is TASK-READY (28 entries,
  run audit-20261001-0323). Compound metafield+type rules are skipped from
  the payload by design (never widened); xlsx keeps them.
- NEXT STORE ACTION (collections chat's lane): apply the 28-rule tranche.
  Recommended order: zero-add pilot (Keychains 0, Ball Pumps 0, San Jose 0,
  Slides +1 — proves all 3 kinds) → Soccer Jerseys (+427 on 14,355, biggest
  blast radius) → rest. Verify each step with a fresh
  collections_audit --members run (counts ≥ before, no RULE_LOST, applied
  rows leave the Ready sheet).
- Colour tier (11) stays human-gated; 'red'⊂'Predator' substring trap.

## Audit app changes shipped 2026-10-01 (all committed)
- collections_audit.py: token-level "soccer" strip for whole-type titles,
  SubCat tag tier (vocab = config addon list + live SubCat_* tags; unmapped
  stays REVIEW), subcat in _rule_matches, _payload on candidate rows
  (stripped from CSV), payload emitted every candidates run.
- deliverable.py: build_rules_payload + _condition_spec emitting the
  task-native contract above.
- passes.py + config.py + audit-ignore.json: FILLED_ACKNOWLEDGED for
  allow-listed manual values (club: No Club, Elite Sport Institute, Metro
  Stars, Mutiny) — club now FILLED_ACKNOWLEDGED=26, FILLED_ORPHAN=0.
- rules.py: choice-list WARN threshold 110→100 (club 88 still INFO).
- HOW-IT-WORKS §9 records the live forecast confirmation. README gained
  the FILLED_ACKNOWLEDGED row.
- Ticket 3 items NOT implemented on purpose: #1 surname memo (decided +
  shipped), #2 reason codes (already in source), #5 variant metafields
  (documented ignore).

## Expected next fresh Full Audit (after 06:00 player sync)
- player SYNC_MISS 78 → 0 (batch-2 back-catalog fills), blanks ~237 → ~159,
  NO_RULE_VALUE → ~143 (James 76 + Rodrigo 67 only), filled-check
  divergents ~17 (multi-player-title noise class), orphans ~12.
- club: blanks 13 (Goretzka-class audit-heuristic noise, no store risk),
  FILLED_ACKNOWLEDGED=26, FILLED_DIVERGENT=12 (merchant fix list).
- If SYNC_MISS persists after a player-sync run: investigate task runs,
  not the rules.

## Merchant lists (pending, unchanged)
- Player re-tags (12): James, Rodrigo → real names; Estêvão→Estevao,
  João Pedro→Joao Pedro, Andreas Christensen→Christensen, Jules
  Koundé→Kounde, Karim Adeyemi→Adeyemi, Anthony Gordon→Gordon, Diogo
  Dalot→Dalot, Noussair Mazraoui→Mazraoui, Lisandro Martinez→Martinez,
  Frenkie de Jong→De Jong.
- Club corrections (12 FILLED_DIVERGENT): stored→predicted per
  filled-check-club CSV.

## Parked / known noise (do not re-litigate)
- Parked: colour tier; compound Title+Type payload support (needs task-side
  conditions array); Henry-Martín keyword gap (~5, risky vs Kessler);
  Charly "León" accent gap (1); 4 ZERO_WITH_RULE dead collections;
  Villareal title typo; Quiñones MULTI_SOURCE; No Club merchant decision;
  variant-metafield blind spot (On Sale UNKNOWN_RULE_VALUE ×4);
  notes-persistence; weekly scheduler.
- Noise classes: Goretzka-class club KEYWORD_GAP; Felix/Brandt/Henry player
  KEYWORD_GAP; 17 multi-player-title divergents; subcat WILL_REPLACE 3
  oscillators + UNPREDICTED_EXISTING 7 + 1 Topps tin; country USA-vs-X ×3;
  benign COUNT_DROPs from unpublished products (baseline re-saves).