# BRIEF-v2 — sw-audit: absorb the remaining manual-audit methods

Read CLAUDE.md first; every locked decision there still holds (read-only, blanks-only,
matcher parity, no currentBulkOperation, collections pinned 2026-07). Keep the existing
CSV/JSON outputs and their columns unchanged — only add. Keep `python -m unittest discover
tests` green; extend fixtures for every new pass. Style: minimal, no comment bloat, stdlib
only (openpyxl becomes an optional dep like reportlab). When done, update README.md
(outputs table + commands) and append the new locked decisions to CLAUDE.md.

Work in three phases. Finish and test each before starting the next.

---

## Phase 1 — new passes over the EXISTING products cache (no new network calls)

### 1a. Filled-value cross-check (`pass_filled_check` in passes.py)
For ACTIVE products where a filter is FILLED, run the same task matcher:
- `FILLED_ORPHAN`: stored value is not any rule canonical for that filter (this is the
  90-catalog-vs-87-rules discrepancy surfaced directly). Aggregate: value → count; also
  emit per-product rows.
- `FILLED_DIVERGENT`: matcher predicts a DIFFERENT canonical than stored (info only —
  tasks are add_only and will never change it; say so in the column docs).
- No finding when matcher predicts nothing or agrees.
Output: `filled-check-<filter>-<stamp>.csv` (handle, title, filter, finding, stored_value,
predicted_value, admin_url, storefront_url) + orphan value counts into the fill-rate
summary JSON under `filled_orphans` per filter. Wire into audit.pipeline after the blanks
pass. Skip silently in fallback mode (no rules).

### 1b. Rule-delta coverage forecast (new CLI mode)
`python audit.py --forecast NEW-rules.json --filter player [--cached]`
Input file: same shape as keyword_rules (`[{"canonical_value","safe_keywords"}]`).
Compute against the cached catalog's ACTIVE blank products for that filter:
- per new canonical: how many blanks its keywords would fill (apply the merged compiled
  set = existing rules + new rules, longest-wins, so existing rules can still steal a
  longer match — that is the real task behavior);
- collision lint BEFORE counting: any new keyword that normalizes identical to an
  existing keyword of a different canonical (permanent ambiguity) or duplicates within
  the delta → report and still count, flagged;
- totals line: "N new rules would fill M of B current blanks".
Output: `forecast-<filter>-<stamp>.csv` (canonical, keyword_count, blanks_filled,
flags, sample_title) + console summary. Forecast mode runs no other passes and never
touches the network unless the cache is missing.

### 1c. Sibling precision (edit `pass_fill_rates`)
Group active products by each full `group_*` tag value (a product can carry several).
`multi_member_groups` = tag values with ≥2 active products. Replace the current
`sibling_scope` block with: products_in_multi_member_groups, filled_in_multi_member_groups,
multi_member_groups count, plus keep products_with_group_tag. The chats' health claim is
"fill vs multi-member products ≈ 100%" — this makes that number exact. Update the test
fixture: give p13's group a second member so both branches are exercised.

### 1d. Markdown snapshot (`report.write_snapshot_md`)
Emit `catalog-snapshot-<YYYY-MM-DD>.md` alongside the summary JSON, matching the format
of docs/catalog-snapshot-aug11-2026.md in spirit: metric table WITH a delta column vs the
previous summary (blank deltas on first run), sections for fill rates, type distribution,
SubCat/addon counts, filled-orphan counts, and a one-line provenance footer (generated_at,
rules file date, cache file). This is the doc-chain artifact; keep it terse and diffable.

Acceptance for Phase 1: unit tests for 1a (orphan + divergent + agree fixture products),
1b (a delta rule filling a known fixture blank; a colliding keyword flagged), 1c (both
sibling numbers), plus a --cached smoke run producing all new files.

---

## Phase 2 — SubCat classification simulator (parity port, like matcher.py)

New module `subcat_sim.py`: a verbatim port of the SubCat Mechanic task's classification
logic, so the audit predicts what the Sunday run WILL assign and diffs it against actual
tags. Semantics that MUST match the Liquid exactly (do not normalize/improve):

- classification_type = product type mapped case-insensitively through
  cfg["subcat"]["product_type_map"] (already in task-configs.json); unmapped keeps type.
- Most checks are plain SUBSTRING `in` on the lowercased title (NOT word-boundary).
- `title_words` (used ONLY for " gk " and " jr " checks): lowercased title, remove ’ and '
  (no space), then each of - / _ . , ( ) [ ] : ; & → space, pad with leading/trailing space.
- Ordered decision tree by classification_type (first hit wins inside each chain):
  - Air Freshener → SubCat_Air-Freshener ; Enamel Pin → SubCat_Enamel-Pin ;
    Scarves → SubCat_Scarf ; Mini Figures → SubCat_Mini-Figure ; Posters → SubCat_Poster ;
    Water Bottles → SubCat_Water-Bottle ; Shin Guards → SubCat_Shin-Guards ;
    Field Player Gloves → SubCat_Field-Gloves ; Goalkeeper Gloves → SubCat_GK-Gloves
  - Decal / Sticker → SubCat_Decal UNLESS title contains any of: panini, sticker album,
    sticker packet, sticker box (then no prediction)
  - Accessories, in order: magnet→SubCat_Magnet ; lanyard→SubCat_Lanyard ;
    keychain|key chain|key ring|keyring→SubCat_Keychain ; armband|captain→SubCat_Armband ;
    glove wash→SubCat_Glove-Wash ; grip spray|magic grip→SubCat_Grip-Spray ;
    tape|kinesiology→SubCat_Sock-Tape UNLESS hat|snapback|cap ;
    pump→SubCat_Ball-Pump UNLESS heat|jumpman ;
    then EXCLUSIONS predicting nothing: lace|shoelace ; tactic|clipboard ; patch ;
    guard lock|guard stay|shin guard sleeve ; headband ; neck warmer|neckwarmer|snood ;
    mouthguard|mouth guard ; training vest|practice vest ; flag ; nameset ;
    referee|wallet ; ELSE → SubCat_Accessory-Other
  - Hats: snapback→SubCat_Hat-Snapback ; beanie|knit→SubCat_Hat-Beanie ;
    bucket→SubCat_Hat-Bucket ; else SubCat_Hat
  - Socks: grip|trusox|grip sox→SubCat_Grip-Socks ;
    sand|beach|tilos|tilo→SubCat_Sand-Socks ; else SubCat_Soccer-Socks
  - Bags: sackpack|gymsack|gym sack→SubCat_Sackpack ; shoe bag|cleat bag→SubCat_Cleat-Bag ;
    ball bag|mesh bag→SubCat_Ball-Bag ; backpack→SubCat_Backpack ;
    duffel|duffle→SubCat_Duffel ; else SubCat_Bag-Other
  - Balls: mini→SubCat_Ball-Mini ; futsal→SubCat_Ball-Futsal ;
    training|club→SubCat_Ball-Training ; match|pro|official→SubCat_Ball-Match ;
    else SubCat_Ball
  - Footwear: slide|sandal|adilette→SubCat_Slides ; else nothing
  - Soccer Collectibles: funko→SubCat_Funko-Pop ;
    trading card|panini|topps|adrenalyn|match attax→SubCat_Trading-Card ;
    else SubCat_Collectible
  - Misc, in order: funko→SubCat_Funko-Pop ; minix|mini figure→SubCat_Mini-Figure ;
    trading card|panini|topps→SubCat_Trading-Card ; glove wash→SubCat_Glove-Wash ;
    grip spray|magic grip→SubCat_Grip-Spray ; armband|captain→SubCat_Armband ;
    tape|pre wrap|pre-wrap|kinesiology→SubCat_Sock-Tape UNLESS hat|snapback|cap ;
    action figure|banbotoys|bus figure→SubCat_Mini-Figure ;
    ball pump|essential pump|hyperspeed pump→SubCat_Ball-Pump ;
    air freshener→SubCat_Air-Freshener ; else nothing
  - Shorts: goalkeeper OR " gk " in title_words OR padded → SubCat_GK-Shorts ; else nothing
  - Pants: goalkeeper OR " gk " → SubCat_GK-Pants ; else nothing
  - Compression: goalkeeper OR " gk " OR padded → SubCat_GK-Padded ; else nothing
- NOTE the intentional asymmetry: "pre wrap"/"pre-wrap" reach Sock-Tape only under Misc,
  not Accessories. That asymmetry is the known live bug this simulator exists to surface —
  port it faithfully; do NOT fix it in the simulator.

New pass `pass_subcat_sim(products, cfg)` producing `subcat-sim-<stamp>.csv`:
finding ∈ MISSING_SUBCAT (predicted, none present — Sunday run will add),
WILL_REPLACE (predicted differs from existing — replace_incorrect is ON, Sunday changes
it; persistent rows after a Sunday = investigate), UNPREDICTED_EXISTING (existing tag the
tree cannot derive — manual, info), plus eligibility columns: predicted_eligible,
currently_eligible (vs the 29-list) so eligibility flips are filterable. Console line with
finding counts. Wire into audit.pipeline; add fixture products covering each finding, and
one Mueller "Pre Wrap" pair (Accessories-typed vs Misc-typed) asserting the asymmetric
predictions — that is the regression test proving parity.

---

## Phase 3 — collections: members, Adds math, merchant deliverable

### 3a. Opt-in members pull
Routine health runs stay light. New flag `python collections_audit.py --members` runs a
SECOND bulk op with this validated 2026-07 shape (child lines carry __parentId = the
collection gid; strip the connection down in the real bulk query per the no-pagination
rule, i.e. drop the first: args):

    { collections { edges { node { id products { edges { node { id } } } } } } }

Cache to output/catalog-collections-members.jsonl. Loader returns collection_gid →
set(product_gid).

### 3b. Adds / Count After
For every AUTOMATABLE candidate (and colour-verify tier from 3c), evaluate the suggested
rule against the ACTIVE products cache: metafield-equals rules match on the parsed filter
fields; count matching products NOT already members → Adds; Count After = count_now +
Adds. Requires both caches; if members cache is absent, emit candidates without Adds
exactly as today (columns present but blank) and say so once in the log.

### 3c. Candidate-engine tail fixes (find_candidates)
Apply the locked rules from docs/HANDOFF-aug2026-data-session.md §Methodology:
- force Review: title contains "Jersey:" or matches player-PDP shape; title contains
  "custom"; brand-only titles (title reduces to a known vendor name); accented/period/"de"
  names (already partial — extend to periods and " de ");
- colour rules: only valid as Type=Footwear AND Title contains colour → separate tier
  "COLOUR_VERIFY" (not AUTOMATABLE, not REVIEW);
- Type anchor: when the title names a type AND a club/country/player/tournament entity,
  suggested rule = metafield rule (+ Type only when the title is NOT broad
  ("& Accessories", "& Gear", "Collection" → metafield rule alone));
- bare Type= allowed only for genuine whole-type collections: title ≈ the type name AND
  count_now ≥ 50% of that type's active product count (needs the products cache).

### 3d. Merchant deliverable
`python collections_audit.py --members --deliverable` additionally writes:
- `Collections-to-Automate-<stamp>.xlsx` (openpyxl, optional dep — friendly skip message
  if missing): TWO sheets, "Ready to Automate" (AUTOMATABLE + COLOUR_VERIFY) and
  "Needs Review". Columns exactly: ID · Handle · Title · Count Now · Suggested Rule ·
  Adds · Rule type · Count After · Approve · Note. ID column formatted as TEXT (the
  1.5E+11 lesson) and hyperlinked to admin; Handle hyperlinked to storefront; Approve =
  blank yellow-filled cell; padded header widths; sort Adds desc.
  Rule type vocabulary: Club (metafield), Country (metafield), Player (metafield),
  Tournament (metafield), Product type, Title + Type, Title colour, Review.
- keep-manual rows excluded entirely from the workbook.
- Optional matching PDF via the existing reportlab path, tier-grouped, hyperlinked.

Acceptance: fixture members jsonl + a candidate whose Adds is asserted; tail-fix unit
tests (one title per new Review trigger, one colour-verify, one broad-title suppressing
the Type anchor); xlsx smoke test only asserts the file exists and sheet names when
openpyxl is installed, skipped otherwise.

---

Final step: update README outputs table + commands, append CLAUDE.md decisions
(simulator parity incl. the pre-wrap asymmetry note, members pull opt-in, forecast mode,
deliverable conventions), run the full suite and a --cached smoke of both entry points.