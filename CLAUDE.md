# CLAUDE.md — sw-audit

Read-only catalog audit for Soccer Wearhouse (soccerwearhouse.com). Bulk-op pull → JSONL cache → scan → delta CSV/JSON/PDF. Deltas are applied by a human via the existing Mechanic tasks — **this codebase must never gain write access to the store**.

## Locked decisions — do not revisit without the operator asking

1. **Never write to the store.** `read_products` scope only. No mutations, ever, not even behind a flag.
2. **Blanks only.** All live filter sync tasks run `write_mode: add_only`, so filled values are settled — the audit flags blanks and config drift, never "wrong-looking" filled values.
3. **`currentBulkOperation` is deprecated on 2026-07.** Polling is by `bulkOperation(id:)`; the in-flight guard uses `bulkOperations(first:5, query:"(status:RUNNING OR status:CREATED) AND operation_type:QUERY")`. Do NOT "simplify" back to `currentBulkOperation` — it will break, and it's ambiguous now that 5 bulk queries can run concurrently per app. Mechanic's bulk ops belong to a different app and never collide with ours. The product audit requires ≥2026-07 semantics: `API_VERSION` stays env-overridable for future upgrades, never downgrades.
4. **Bulk query rules:** no variables, no aliases, connection pagination args are ignored, results are a flat JSONL stitched by `__parentId`. Max 5 connections / 2 levels deep.
5. **Collections job is pinned to API 2026-07** (`config.COLLECTIONS_API_VERSION`). `Collection.sources` / `CollectionConditionsSource` / `...ConditionMetafieldString { values }` only exist there. The inline fragment on `CollectionConditionsSource` is required, and the field is `values` (plural).
6. **`normalize` / `compile_rules` / `match_title` in matcher.py are a verbatim port of the Mechanic Liquid matcher** (`strip_fc` / `phrase_in` are audit-side heuristics from the collection/vocab methodology, outside the parity contract). Parity is the whole point — reason codes are only facts because the audit predicts exactly what the task will do. Never "improve" it. The pipeline is:
   downcase → strip → accent fold (áàâäãå→a, éèêë→e, íìîï→i, óòôöõ→o, úùûü→u, ñ→n, ç→c, ß→ss — nothing else; ø is intentionally NOT folded) → `&`→" and " → `'’`​`-_/.,()[]+:;!?|`→space → collapse whitespace → padded whole-word contains → longest keyword wins → equal-length tie across different canonicals = ambiguous = skip.
7. **Reason codes are facts, not guesses:**
   - `SYNC_LAG` — task matcher hits, product younger than the lag window (default 48h; syncs run daily: club +2h, country +4h, player +6h)
   - `SYNC_MISS` — task matcher hits on an older product; task should have filled it; investigate. Syncs are daily FULL-CATALOG bulk sweeps (status:active), not event-driven, and lag is measured from `created_at` only — never `updated_at` (it bumps on inventory/price changes and would mislabel busy products as perpetual SYNC_LAG). Known edge: a title edited yesterday on an old product shows SYNC_MISS for up to ~a day, until the next sweep fills it.
   - `AMBIGUOUS` — equal-length tie; skipped by task design
   - `INVALID_CHOICE` — matched canonical missing from allowed_values; the task `skip_invalid_choice`s it silently forever (config bug; lint also catches it globally)
   - `KEYWORD_GAP` — a rostered canonical appears in the title but no safe_keyword covers this phrasing (e.g. bare "Davies")
   - `NO_RULE_VALUE` — a catalog vocab value appears in the title but no rule produces it (manual edits / removed rule)
   - `CANDIDATE` — fallback mode only, when no task-configs.json is present
8. **task-configs.json is generated, never hand-edited.** Refresh: export the 5 Mechanic tasks → drop into `task-exports/` → `python make_task_configs.py` → check the printed counts (as of Sept 2026: player 168, club 87, country 46, subcat 29 addon SubCats, vendor_map 34). Task UUIDs are mapped in `make_task_configs.TASK_HANDLES`. A future live-pull would go through Mechanic cache endpoints — swap inside `rules.load_task_rules()` only; audit logic must not care where rules come from.
9. **Fallback guards** (`williams, lyon, sunderland, porto, henry, morgan, santos, juarez, araujo, gordon`) and the min-length-4 rule apply ONLY in no-config fallback mode. In rules mode the safe_keywords themselves are the safety.
10. **The 29-list is the single Job-3 reference.** Theme-side PRIORITY comparison was explicitly descoped by the operator. Job map (operator's external runbook): 1 fill-rate snapshot · 2 filter blanks · 3 addon coverage · 4 vendor audit · 5 collections.
11. **UI holds no logic.** `ui.py` is a thin Tkinter adapter over `audit.pipeline` / `collections_audit.pipeline`; every button maps 1:1 to a CLI command. Passes stay pure functions taking `(products, cfg)`; anything network/file goes through `shopify_client` / `report`.
12. `remove_addon_eligible_when_not_priority` is OFF on the live SubCat task, so stale `addon-eligible` tags persist by design — the audit reports them (`addon-stale-tag` CSV) but they are not bugs.

## Style
Operator preferences: minimal surgical edits, no comment bloat, no JSDoc-style headers, no unnecessary logging, don't rename existing identifiers, stdlib over dependencies (only python-dotenv + optional reportlab).

## Tests
`python -m unittest discover tests` — fixtures cover every reason code, the matcher parity table, coverage/vendor passes, and the export parser. Keep them green; extend fixtures when adding a reason code.

13. **Auth is two-path by design.** Dev Dashboard apps issue no permanent `shpat_`; `get_admin_token()` mints a 24h token via the client credentials grant from CLIENT_ID/CLIENT_SECRET (cached in `output/.token.json`), and `ADMIN_TOKEN` (legacy app or `get_token.py` output) overrides it. `get_token.py` is the one-time authorization-code fallback for the cross-org `shop_not_permitted` case. Don't collapse these into one path.
14. **The filled-value cross-check is informational.** `pass_filled_check` runs the task matcher over FILLED values: `FILLED_ORPHAN` (stored value is no rule canonical — rules/catalog drift) and `FILLED_DIVERGENT` (matcher predicts a different canonical; info only — tasks are add_only and never change it). It is not a to-fix list and does not relax #2: no blanks reason code is ever derived from a filled value. Skipped in fallback mode.
15. **Forecast mode** (`audit.py --forecast NEW-rules.json --filter club|country|player`) runs no other pass and uses the cache (fetches only if the cache is missing and `--cached` is absent). It matches existing + new rules merged (longest wins, so existing longer keywords still steal), collision-lints first (`COLLISION_EXISTING`, `DUPLICATE_IN_DELTA`; flagged rules are still counted), and assumes new canonicals are also added to the task's allowed_values.
