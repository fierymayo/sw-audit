# Google & YouTube vs Online Store channel mismatch — findings

Mode: read-only investigation. No store writes, no Mechanic changes, no commits.
Prepared: 2026-10-09 (UTC). sw-audit scope: `read_products` only.

## 0. Inputs and provenance

| Input | Detail |
|---|---|
| Mismatch CSV | `google-channel-mismatches-20261007-f83a.csv`, 482,645 bytes, sha256 `a9e0a2352511c011e3c1ece190197923df2ac031173a296e621a687792a4895e` |
| Fresh products pull | bulk op `gid://shopify/BulkOperation/8568223957107`, API 2026-07, query `status:active`, pulled 2026-10-09 18:20 UTC, 18,988 ACTIVE products / 99,225 variants / 13,039 `mechanic.out_of_stock_at` metafields |
| Mechanic task code | pasted by the operator: `fa11f4a5-16bd-46ad-b736-31b950fe2e6a` (Unpublish OOS after X days), `dbfb4d2f-18ea-44fe-80ad-e127f81ab8f0` (Publish back-in-stock), `45da3758-a652-459e-bd17-df20777954e7` (collection reorder, not a publisher) |

### CSV validation (independent re-check)
All of the following were verified by parsing every row, not sampled:

- 2,884 data rows, LF line endings, no BOM, 7 columns.
- `status` — single distinct value `ACTIVE` (2,884).
- `google_youtube_published` — single distinct value `true` (2,884).
- `online_store_published` — single distinct value `false` (2,884).
- `online_store_url` — blank on all 2,884 rows.
- `product_id` — 2,884 distinct, zero duplicates. `product_gid` — 2,884 distinct, zero duplicates, and every gid equals `gid://shopify/Product/<product_id>` (zero mismatches).
- `title` — 2,873 distinct, i.e. **11 titles are shared by more than one product id**. Not an error, but any de-dupe by title would silently drop rows.
- Zero leading/trailing whitespace in titles.

## 1. Phase A — every row classified (2,884 / 2,884, no residue)

Sellability is judged at the **variant** level: a product is sellable if any variant is untracked, has `inventoryPolicy: CONTINUE`, or has `inventoryQuantity > 0`.

| Bucket | Meaning | Count |
|---|---|---|
| A1 | Sellable now, **and** carries `mechanic.out_of_stock_at` | **566** |
| A2 | Sellable now, no timestamp | **11** |
| B1 | Out of stock, carries `out_of_stock_at` | **2,280** |
| B2 | Out of stock, no timestamp | 0 |
| C | Already resolved since the CSV was cut (Online Store URL present today) | **27** |
| D | Unexplained / not in the fresh ACTIVE pull | **0** |

Every row resolved to a bucket. No product in the CSV has been deleted, archived or drafted in the two days since.

### Store-wide context
- 12,581 of 18,988 ACTIVE products have no Online Store URL today. Only 2,884 of those are also on Google & YouTube; the other ~9,697 are off both channels.
- 13,039 ACTIVE products carry `mechanic.out_of_stock_at`.

### Breakdowns

Type (top):

- A1: Jerseys 514, Footwear 30, Jackets 6, T-Shirts 6, Shorts 3, Socks 2, Balls 2, Hoodies 2
- A2: Accessories 6, Patch 3, Jerseys 1, blank type 1
- B1: Jerseys 1,860, Footwear 150, Bags 42, Balls 42, Soccer Collectibles 36, Goalkeeper Gloves 22, Jackets 22, Hats 17
- C: Jerseys 27

Vendor (top):

- A1: adidas 283, Nike 218, Puma 59, Charly 2, Pirma 2, Official Sports 1
- A2: Misc 8, Soccer Wearhouse 2, Nike 1
- B1: adidas 905, Nike 720, Puma 440, Charly 53, Umbro 37, Pirma 34

`out_of_stock_at` age bins (as of 2026-10-09):

| Bucket | 0–30d | 31–90d | 91–365d | 365+ | none |
|---|---|---|---|---|---|
| A1 | 14 | 233 | 319 | 0 | 0 |
| B1 | 82 | 443 | 1,755 | 0 | 0 |
| C | 0 | 0 | 27 | 0 | 0 |
| A2 | — | — | — | — | 11 |

No timestamp is older than 365 days; the oldest is 2026-02-24, consistent with the task having been first enabled around 2026-02-24/25 (132 rows share the 2026-02-25 stamp and 275 share 2026-05-21 — those are bulk first-detection batches, not organic events).

`addon-eligible` tag: **96 of the 2,884** carry it — B1 88, A2 5, A1 3. These are the known pending exception: Unpublish OOS has no addon carve-out, so add-on products get pulled off the Online Store along with everything else.

Created-at month is spread across 2015–2026 with no concentration that explains the mismatch (top months: 2024-08 227, 2025-09 221, 2026-05 163, 2025-07 144).

## 2. Root cause (Phase C) — read-only code analysis

Both publishers are configured `sales_channel_names__required_array: ["Online Store"]`, and both resolve that name through `publications(first: 250, catalogType: APP)` → `catalog.apps.nodes.first.title`. Neither task has ever been pointed at Google & YouTube.

**So nothing in Mechanic ever unpublished Google. The mismatch is not something that happened to Google — it is the absence of symmetry.** Unpublish OOS removes exactly one channel, Online Store, and leaves every other publication untouched. Google & YouTube therefore keeps the product indefinitely, pointing at an Online Store URL that 404s. That is the whole of Huni's complaint.

Three independent defects sit underneath the 2,884 rows:

### Defect 1 — asymmetric channel handling (explains all 2,884)
`fa11f4a5` unpublishes only the configured channels (`publishableUnpublish` per publication id, built from `publication_ids`). Google & YouTube is never in that list. There is no companion step that removes, or even reports, other channels. Any product the task hides becomes an Online-Store-less, Google-live product by construction.

### Defect 2 — selection metric mismatch: product sum vs variant stock (explains A1 = 566)
- `fa11f4a5` selects with `status:active AND inventory_total:<=0` (product-level sum), then applies a variant-level exemption check.
- `dbfb4d2f` selects with `status:active AND inventory_total:>=1` (same product-level sum) and republishes only what that query returns.

Because `totalInventory` is a **sum**, oversold variants with negative quantities drag it below zero even when another size is genuinely in stock. All 566 A1 products have at least one variant with positive quantity and a product `totalInventory` of ≤ 0 (range −20 … 0). They qualify for unpublishing and can never qualify for republishing. This is a one-way door, not a lag.

Store-wide blast radius of that door, measured on today's pull:

- **5,557** ACTIVE products have at least one negative-quantity variant.
- **613** ACTIVE products have at least one positive-quantity variant but `totalInventory ≤ 0` — of those **610** are unpublished from the Online Store (566 of them also on Google, the rest off both channels).

### Defect 3 — the out-of-stock timestamp is never cleared on restock (explains A2 = 11, and a 464-product live risk)
The task's own docs say it "clears that timestamp when the product comes back in stock." The script does not. The only `metafieldsDelete` calls are inside the two exempt branches. There is no `shopify/inventory_levels/update` subscription (subscriptions are `mechanic/scheduler/daily+3.hours` and `mechanic/user/trigger` only), and the product query filters `inventory_total:<=0`, so an in-stock product is never even scanned. `dbfb4d2f` republishes but never touches the metafield.

Consequences:

- The 27 bucket-C products were republished today yet still carry `out_of_stock_at: 2026-06-29`. The next time their sum dips to ≤ 0 they will be unpublished on the very next run — the 5-day grace period is silently skipped.
- **464** ACTIVE products store-wide are currently sum-sellable while holding a stale timestamp, i.e. 464 products are one inventory dip away from an instant unpublish.
- The 11 A2 products are the mirror image: all 11 have an exempt variant (untracked or CONTINUE), so the exempt branch deleted their timestamp — but deleting the timestamp does not undo an unpublish that already happened, and with `totalInventory` of 0 or negative (one is −421) they will never be picked up by `dbfb4d2f` either. They are permanently stuck off the Online Store and permanently live on Google. Five of them are `addon-eligible` keychains; one is "Test Product - Can Safely Delete This Order. Do Not Fulfill", which is ACTIVE and live on Google today.

### Secondary observations (not causes, worth knowing)
- `fa11f4a5` only acts when `day_number modulo 3 == 0` on the daily schedule (`day_number` is `%j`, day of year). So it acts roughly every third day, and the year boundary produces an irregular gap. Nothing in the data depends on this, but reported run dates will cluster.
- `in_flight_key` is set with `setex … 21600` and deleted at the end of a successful run; an aborted run leaves the lock for 6 hours and the next runs log "Previous run still in flight; skipping".
- Both tasks are pinned to `shopify_api_version: 2026-01`.
- The 275-row 2026-05-21 and 132-row 2026-02-25 timestamp clusters mean those batches were first *detected* on those dates, not necessarily out of stock since then — first-detection timing, as the task docs warn.
- Searching all 5 files in `task-exports/` for `publishablePublish` / `publishableUnpublish` / `productPublish` / `productUnpublish` returns **zero** matches, so no other locally-exported task publishes anything.

## 3. Phase B — blocked, by design

Verifying the Google & YouTube side first-hand needs `publications` / `resourcePublications`, which requires the **`read_publications`** scope. sw-audit holds `read_products` only (CLAUDE.md locked decision 1), and per the brief no scope was added. So:

- Phase B is **not done**. Every statement in this document about the Google & YouTube channel rests on the Oct 7 CSV, which was produced elsewhere.
- That CSV is already drifting: 27 of its rows (bucket C) changed state within two days. Treat the Google column as "true as of 2026-10-07", not as current.
- The Online Store side **is** first-hand: `onlineStoreUrl` and `publishedAt` come with `read_products` and were pulled today.

## 4. Phase D — verification plan (described, not run)

Post-approval, in this order:

1. Before any Mechanic run, re-pull products and re-cut this classification. Rows that moved buckets in the interim drop out of the proposals rather than being acted on from a stale list.
2. Run the approved Mechanic task in `test_mode` first and diff its intended actions against the proposal CSV row for row. Counts must match exactly; any extra row is a selection bug.
3. Run for real, then re-pull and confirm: zero A1/A2 rows left unpublished on the Online Store, and zero B1 rows left published on Google.
4. Add a recurring sw-audit check — "ACTIVE on Google & YouTube **and** Online Store unpublished" — emitted into `history/metrics.jsonl` so the count is tracked over time instead of rediscovered. **This check needs `read_publications`; it cannot be built until that scope is granted.** Until then the nearest proxy sw-audit can compute unaided is "ACTIVE with `onlineStoreUrl` null" (12,581 today), which over-counts by the ~9,697 products that are off both channels.
5. Independently, track the two upstream populations: products with any negative-quantity variant (5,557) and the trap population (613). Both shrink only if inventory is corrected, not if publishing is patched.

## 5. Prevention spec (plain language, for the Mechanic chat to implement)

1. **Symmetric channel handling.** Whatever set of channels a product is hidden from, it must be restored to the same set — one shared configured channel list consumed by both tasks, not one list per task. If the merchant wants Google hidden too, Google goes in that list; if not, the asymmetry must be a deliberate, documented choice rather than a side effect.
2. **Judge stock the same way on both sides, at variant level.** "Out of stock" should mean no variant is purchasable (no untracked variant, no CONTINUE variant, no variant with quantity > 0) — not `totalInventory <= 0`. The unpublish and republish conditions must be exact complements, so no product can satisfy neither.
3. **Clamp or ignore negative quantities** when summing, if any product-level sum is kept at all. A negative variant must never be able to cancel out real stock elsewhere on the product.
4. **One timestamp lifecycle, owned in one place.** Set `out_of_stock_at` when a product becomes unsellable; delete it the moment it becomes sellable again, including on the republish path and including the exempt path. A product that is sellable must never hold a timestamp.
5. **Republish selection must not depend on the timestamp or on the sum.** It should scan products that are unpublished from a managed channel and currently sellable, so a product that fell through the floor earlier gets picked back up rather than staying stuck forever.
6. **`addon-eligible` exception.** Add-on products should be excluded from unpublishing entirely (they are merchandising surfaces, not standalone stock), or given their own explicitly configured channel list. 96 of the current 2,884 are add-ons.
7. **Exempt means never managed.** If a product has any exempt variant, the task should neither unpublish it nor leave it in a half-managed state. Clearing the timestamp is not enough — the 11 A2 products prove that clearing without republishing strands them.
8. **Test mode and a dry-run diff** stay mandatory for every mutating run, and every run should log its scanned/acted/skipped counts so a regression shows up as a count, not as a merchant complaint.

## 6. Merchant policy decision — both options, neutrally

The 2,280 B1 products are genuinely out of stock. There are two defensible answers and this document does not pick one.

**Option 1 — hide everywhere.** Remove out-of-stock products from Google & YouTube as well, so the two channels agree.

- A shopper on Google never lands on a dead or unbuyable page.
- No Google Merchant Center disapprovals or "price/availability mismatch" warnings for these items.
- Loses whatever residual free-listing impressions and accumulated ranking those 2,280 items carry; when they come back in stock they re-enter the feed cold.
- Requires the republish path to be reliable, or returning stock stays invisible on Google.

**Option 2 — keep the pages live.** Keep the products on the Online Store (and Google) while out of stock, and let the storefront show an out-of-stock state.

- Keeps SEO equity, Google listings, back-in-stock email signups and inbound links on durable URLs.
- No publish/unpublish churn at all, which removes defects 1–3 as a class rather than patching them.
- Shoppers see unbuyable products, which costs conversion rate and invites "why is this listed" complaints.
- 2,280 out-of-stock items become browsable in collections unless collection sorting and filtering push them down (the collection-reorder task `45da3758` already sorts out-of-stock items to the end).

A third, narrower position exists if the merchant wants it: keep the pages live only where stock is expected to return (restockable jerseys, current-season product) and hide retired or one-off items. That needs a durable signal in the data — a tag or metafield — before it can be automated.

## 7. What is NOT established here

- Nothing about the Google & YouTube publication state was verified first-hand (see §3).
- The counts for Mechanic task behaviour are read from code, not from run logs. Mechanic run history was not inspected.
- No judgement is offered on whether the 5,557 negative-inventory products are a fulfilment problem, a sync problem or intentional oversell configuration.
