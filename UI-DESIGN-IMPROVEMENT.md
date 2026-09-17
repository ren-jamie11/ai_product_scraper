# UI-DESIGN-IMPROVEMENT — Results view, at a glance

## Context

The pipeline is done and trusted: tags → clusters → themes, with every mention traced to
its sentence. The results view renders all of it, but it renders it as an *index*, not a
*picture*. On first open a seller sees a summary strip, a paragraph of instructions, and
a stack of collapsed theme rows whose only size cue is a mono counts line. Nothing on the
first screen says "these three things matter most, and here is whether customers or
sellers are saying so."

Two measured facts drive the design (numbers from the nine parsed runs on disk):

- Reviews supply ~70–80% of feature mentions; complaints are 100% reviews. So the current
  "All" order is really the reviews order, and the source toggle deliberately keeps that
  order (`clusterView`: "Order never changes"). The seller-side ranking is never visible.
- Feature lists run 18–57 clusters and 8–12 themes; complaints 3–17 clusters. That is too
  many to scan as cards, but exactly the size a horizontal bar list handles well.

**Goal:** an Amazon seller opens a results page and, without a click, knows the top
themes by mentions in each list and how much of that comes from listings vs reviews; can
flip to the customer's or the seller's native ranking; can type "handle" and see only what
concerns handles; and can drill from a bar to a theme to a cluster to a tag to the sentence.

**Untouched:** fonts, colours, spacing tokens; tagging / clustering / theming code and
prompts; the groups, group detail and run views; every data file. The results endpoint
(`pipeline/results.py`) already returns everything needed, so **no backend change** is
planned. All work is in the results section of `static/index.html` (CSS ≈ lines 245–357,
markup ≈ 496–537, JS ≈ 1990–2370 plus the hover/panel code that follows).

## Decisions settled in scoping (2026-09-17)

| Area | Decision |
|---|---|
| Summary graphic unit | Theme bars, each expandable in place to its cluster bars. Unthemed lists show cluster bars directly. |
| Placement | One overview panel at the top of the page, two columns: **Features** and **Complaints**. Care is list-only. |
| Bar encoding | Length = mentions; segmented into listing share (solid) and review share (lighter) in the list's colour. |
| Bar click | Scrolls to the theme/cluster, opens it, flashes it. Chart stays as the map. |
| Bar count | All themes; all member clusters on expand; unthemed lists top 10 with "show all". |
| Native ranking | On Listings / Reviews, themes and clusters re-sort by that source's mentions. Items with nothing from that source are hidden (as today). |
| Numbering | 01, 02… = position in the current view. Re-numbered per source. |
| All ranking | Raw total mentions (unchanged). The segmented bar shows the imbalance. |
| Rank shift | "listings #11 · reviews #2" line on cluster cards and theme rows. |
| Search scope | Theme title + summary, cluster title + description, and every tag chip in the current source view. |
| Search effect | Non-matches hidden. Matching themes auto-open while searching; matched words marked in titles and chips. Overview follows the search. Sticky bar shows match counts. |
| Sticky bar | Search · All/Listings/Reviews · jump links with counts (Features 35 · Complaints 17 · Care 7) · Expand/Collapse all · Group into themes (only when needed, as now). |
| Summary strip | Replaced by one mono line: `35 features · 17 complaints · 7 care · 10 products · 92% of mentions traced`. |
| Lede paragraph | Dropped. |
| Default state | Overview visible, all themes collapsed, expansion still remembered per parse. |
| Open cluster | Two blocks (From listings / From reviews), unchanged. |
| Gap badges | Small pill on feature cluster cards: **under-advertised** (customers say it, listings don't) / **unconfirmed** (listings claim it, reviews don't). Features list only. |
| Coverage | "in N of M products" becomes M dots, N filled, plus `N/M`. |
| Copy to clipboard | Not wanted. |
| State | Source and search are session-only. Theme/cluster expansion stays in `localStorage` as today. |
| Order | 1 ranking → 2 overview → 3 search + bar → 4 insights. |

## Working principles

- Everything is computed in the browser from `resultsState.data`; the results endpoint
  is not changed. A tiny **view model** step (`buildViews`) runs once per render and every
  renderer reads from it, replacing the ad-hoc `clusterView` cache.
- Bars are plain `div`s. No chart library. Colours come only from the existing tokens:
  `--celadon` for features, `--iron` for complaints, `--cobalt` for the neutral accent.
- Reuse: `stat()`, `plural()`, `esc()`, `.seg`, `.pill`, `.btn.ghost`, `.note`, `.blank`,
  `.hint`, `.sbar`, `.thm`, `.cl`, `.bub`, `mark`.
- **Caution from memory:** two lines in `static/index.html` contain a literal NUL byte
  used as a Map-key separator. `Edit` cannot match them. If a change touches those lines,
  patch with a short Python script instead.

---

## Phase 1 — Native ranking per source

**What changes:** the source toggle re-ranks instead of only re-counting.

`static/index.html`, results JS:

1. Replace `clusterView()` + `resultsState.views` with `buildViews()` called at the top of
   `renderResultsBody()`. For each section it produces:
   - per cluster: `{ all, listing, review }` mention counts (sum of `count` over
     `listing_tags` / `review_tags`; `all = total_tags`), per-source `asinSet` (from
     `occurrences[ref].asin`, as today), `preview` per source, and **ranks**
     `rank.all / rank.listing / rank.review` (1-based, ties broken by server order; `null`
     when the cluster has no mentions from that source). Ranks are computed for all three
     sources every time, because Phase 4 needs them regardless of the current view.
   - per theme: the same shape, summed over member clusters (`asinSet` = union).
   - `order`: the list of visible clusters (and of visible themes, and of visible member
     clusters within each theme) sorted by mentions in the current source, desc, ties by
     server order. On `all` this equals the server order exactly.
2. `sectionHtml`, `themeHtml`, `clusterHtml` iterate `order` instead of `s.clusters` /
   `t.cluster_ids`, and print the position in `order` (padded) as the `01` label instead
   of `c.number` / `t.number`.
3. Section header note and theme counts already use the view numbers; make sure they read
   from the view model so they stay consistent.

**Verify**
- Olive trees, Listings: features re-order; the bar total equals 91 listing mentions
  (`data/olive-trees/…/clusters.json`), complaints section shows "Nothing from listings".
- Print the expected order to compare against the screen:
  ```
  python -c "import json;d=json.load(open('data/olive-trees/2026-09-11_155256/parse-2026-09-16_162551/clusters.json',encoding='utf-8'));s=d['sections'][0];occ={o['occ_id']:o for o in s['occurrences']};u={x['uid']:x for x in s['unique_tags']};print(sorted(((sum(1 for uid in c['uids'] for o in u[uid]['occ_ids'] if occ[o]['body_type']=='listing'),c['title']) for c in s['clusters']),reverse=True)[:8])"
  ```
- All view is pixel-identical in order to today (numbers match `clusters.md`).
- Toggling back to All restores the numbering.


**Settled in Phase 1 (built 2026-09-17).** `clusterView` and its per-render cache are
gone; `buildViews()` runs at the top of `renderResultsBody()` and stores one view per list
in `resultsState.views` (`sectionView(s)` reads it). Every cluster and theme view carries
`mentions`, `asins` (a Set) and `rank` for all three sources, and `number` is the rank in
the current source, so a cluster inside an open theme still shows its rank in the whole
list (01, 02, 17, 29 …), not its position in the theme. Theme mentions and products are
summed from member clusters rather than read from `themes.json`; on "All" the two agree
exactly (asserted on olive trees). Checked by executing the page's own render functions in
Node with DOM stubs (`scratchpad/test_render.js`) against olive trees, wooden serving bowls
and wood picture frames in every source and expansion state: All order equals the server
order, Listings order equals the count computed straight from `clusters.json`, numbering
has no gaps, and complaints vanish under Listings.

---

## Phase 2 — Overview panel (the at-a-glance graphic)

**Markup:** a new `<div class="ov" id="res-ov">` between the sticky bar and `#res-body`.
Two `.ovcol` columns (Features, Complaints); each has a head (list title, mono note
`N clusters · N mentions`), a one-time legend (`▮ listings ▮ reviews`, hidden on a single
source), and rows.

**Row (`.ovrow`)**: `[caret?] [label] [bar] [value]` in a CSS grid
`auto minmax(0,1fr) minmax(120px,1.4fr) auto`. Label is the theme/cluster title, single
line, ellipsis, `title=` full text. Bar is a track with two segments whose widths are
`% of the column's max mentions`: listing segment solid list colour, review segment same
colour at reduced opacity. Value is mentions in mono. `title` on the row gives the
breakdown: `71 mentions · 20 listings · 51 reviews · in 8 of 10 products`.

- Themed list: one row per theme in view order. Caret toggles that theme's member cluster
  rows beneath it (indented, thinner track, scaled to the **same** column max so a cluster
  bar is directly comparable to a theme bar). Open state kept in a session-only
  `resultsState.ovOpen` Set.
- Unthemed list: top 10 cluster rows plus a `.lnk` "Show all N"; session-only flag.
- Listings / Reviews source: single-segment bars, native order from Phase 1.
- Empty or failed list: the column shows a short `.blank` message so the layout holds.
- Under 640px the two columns stack.

**Click on label or bar** → `revealItem(key)`: add the theme key (and the cluster key if it
is a cluster row, plus its parent theme's key) to `resultsState.expanded`, clear the
list's collapsed state, `persistExpanded()`, `renderResultsBody()`, then
`querySelector('[data-k="…"]').scrollIntoView({block:"start"})` and add a `.flash` class
removed after ~1.2 s. `.thm, .cl { scroll-margin-top: <sticky bar height + 12px> }` so
the target lands under the sticky bar. `.flash` = cobalt outline that fades; honours
`prefers-reduced-motion`.

**Verify**
- Olive trees: 11 feature theme bars, 8 complaint bars; the longest feature bar is the
  76-mention theme; listing segment visibly smaller than review segment.
- Wooden serving bowls (unthemed, 18 clusters): 10 cluster bars + "Show all 18".
- Wood picture frames (57 clusters, 12 themes): panel stays under one screen collapsed.
- Click the third feature bar → page scrolls, that theme is open and flashes, its clusters
  are visible; reload → it is still open (expansion persisted).
- Switch to Listings → bars re-order and become single-segment; complaints column shows
  "Nothing from listings".


**Settled in Phase 2 (built 2026-09-17).** `overviewHtml(key)` renders one `.ovcol` per
list in `OVERVIEW_LISTS` (features, complaints) into `#res-ov`, which sits between the
sticky bar and the sections. Rows are CSS-grid `caret · label · track · value`; the track
holds up to two `<i>` segments sized as a percentage of the column's largest top-level bar,
so a cluster bar unfolded under a theme is on the same scale as the theme bars around it.
Segment colour is the list's chip colour; the review share is the same colour at 45%
opacity, and a single-source view draws one solid segment. The legend only appears on
"All". A themed list shows every theme; an unthemed list shows `OVERVIEW_TOP` (10) clusters
and a "Show all N" link. Caret and show-all state live in `resultsState.ovOpen` /
`resultsState.ovAll` (session-only); clicking anywhere else on a row calls
`revealItem(list, key, parent)`, which opens the list, the theme and the cluster, persists
expansion, re-renders, scrolls the target under the sticky bar (`scroll-margin-top`) and
flashes a cobalt outline for 1.3 s (reduced motion: no smooth scroll, and the page already
disables all transitions). Row `title` attributes carry the full breakdown. The Node render
harness now asserts overview row counts too: olive trees 11 + 8 theme rows, wooden serving
bowls 10 + 3 with a show-all, wood picture frames 10 + 10.

---

## Phase 3 — Search and the sticky bar

**Sticky bar (`#res-bar`)** becomes: search field · source seg · jump links ·
Expand/Collapse all · Group into themes (conditional) · progress · error. The search field
is a plain `<input type="search" id="res-q">` styled like `.fld input` but inline (height
matches `.seg`), placeholder *"Filter… e.g. handle, pot, appearance"*, with a clear `×`.
`/` focuses it, Escape clears it. Input is debounced ~120 ms into `resultsState.query`
and a re-render.

**Jump links (`.jump`)**: `Features 35 · Complaints 17 · Care 7` as links to
`#sec-<key>` (sections get ids); counts come from the view model so they follow the source
filter, and while a query is active they read `Features 4 of 35`. Clicking scrolls with
the same offset as `revealItem`.

**Slim summary line**: `#res-sum` loses the `.sumstrip` tiles and becomes a single mono
line (`.slim`): `35 features · 17 complaints · 7 care · 10 products · 92% of mentions
traced to a sentence`. A failed list reads `complaints failed` in iron. `res-lede` markup
and its JS references are removed.

**Search semantics** (in `buildViews`, so ranking, overview and sections all agree):

- Normalise query and haystacks: lowercase, collapse whitespace. Split the query on
  whitespace; every term must appear as a substring somewhere in the item's haystack.
- Cluster haystack = title + description + display text of the tags in the current source
  view. Cluster matches → it is visible. Its `preview` puts matching chips first so the
  reason is visible while collapsed.
- Theme haystack = title + summary. A theme whose own text matches shows **all** its
  member clusters; otherwise it shows only matching clusters and is visible only if at
  least one matches. A theme with matches is rendered open while the query is non-empty
  (`expanded` is not modified; the open state is an override in the view model).
- Highlighting: a helper `hilite(text, terms)` escapes and wraps each term hit in
  `<mark>`; used for theme titles/summaries, cluster titles/descriptions and chip text.
  Chip handlers already use `closest(".bub")`, so a `<mark>` child is safe; confirm in
  the hover/panel code (`mouseover` handler ≈ line 2589, `openPanel` ≈ 2615).
- Overview rows are built from the same filtered view, so the chart follows the search;
  the "top 10" cap for unthemed lists is lifted while searching.
- Empty result: each affected section and overview column shows `.blank`
  *"Nothing matches "xyz""* with a clear link.

**Verify**
- Olive trees, type `pot`: only pot-related clusters remain, their themes are open, "pot"
  is marked in chips and titles, jump links read `Features n of 35`, overview shrinks to
  the same items. Clear → everything returns and expansion is exactly as before.
- Type `handle` on ceramic mugs: matches via tag text alone (a cluster whose title lacks
  the word).
- Type two words (`real look`): both must match.
- Listings + query: hidden-by-source items never match.
- Narrow to 600px: search stays usable, bar wraps to two rows, nothing overflows.


**Settled in Phase 3 (built 2026-09-17).** The sticky bar is now `search · All/Listings/
Reviews · jump links · Expand all · Group into themes`; the "Show" label went. The summary
tiles became one `.slim` mono line (`35 feature clusters (409 mentions) · 17 complaint
clusters (76 mentions) · 7 care clusters (35 mentions) · 10 products · 93% of mentions
traced to a sentence`) and the lede paragraph is gone. Filtering lives in `buildViews`:
the query is split on whitespace, every term must be a substring of the item's lowercased
text, a cluster's text is title + description + the tag chips of the current source, a
theme's is title + summary. A theme matching on its own text keeps all its clusters
(`tv.self`); otherwise it keeps the matching ones, and while a query is active every theme
with matches renders open in both the overview and the sections without touching the
persisted `expanded` set. Matching chips move to the front of a collapsed card's preview.
`hilite(text, terms)` escapes and marks hits in theme titles and summaries, cluster titles
and descriptions, chip text and overview labels. `sv.count` / `sv.total` (shown vs present
in this source) feed the jump links ("Features 3 of 35"), the section notes and the empty
states, which offer a "Clear the filter" link. Jump links scroll instead of navigating,
because a hash change would re-route. `/` focuses the search, Escape clears it, and the
input is debounced 120 ms. Checked with the Node harness on olive trees ("pot", "real
look", "zzzz"), ceramic mugs ("handle": matches through tag text alone, 8 of 31) and
wooden serving bowls, plus Playwright screenshots at 1280 px and 600 px with no console
errors. One bug caught by the harness: clusters shown only because their theme matched had
no number, so numbers are now assigned to every present cluster before filtering.

---

## Phase 4 — Insight helpers

All computed in `buildViews` from ranks and per-source mentions already there.

1. **Rank shift line** on cluster cards and theme rows, after the counts line, mono:
   `listings #11 · reviews #2`; a missing source reads `not in listings`. Shown in every
   view (it is the insight, not the filter).
2. **Gap badges** on feature cluster cards only (complaints have no listing side; care
   is too small). Shares are normalised by source volume so review dominance doesn't
   bias them: `ls = listing mentions / section listing mentions`,
   `rs = review mentions / section review mentions`.
   - `under-advertised` (`.pill.warn`): `rs ≥ 2·ls` and review mentions ≥ 5.
   - `unconfirmed` (`.pill.na`): `ls ≥ 2·rs` and listing mentions ≥ 3.
   Thresholds are named constants at the top of the results JS with a comment; tune after
   one look at real output (olive trees and wood frames are the test beds).
   Pill `title` explains the rule in one sentence.
3. **Coverage dots**: in the counts line, replace `in N of M products` with a `.cov`
   row of M dots (`--cobalt` filled, `--rule` empty) followed by `N/M`; fall back to the
   text form when M > 20.
4. **Small polish** found while building: overview rows get the same gap-badge dot in
   the label when applicable; `Expand all` also opens overview theme rows.

**Verify**
- Olive trees feature list: every card shows both ranks; the top review cluster that is
  weak in listings carries `under-advertised`; a listing-heavy cluster with few reviews
  carries `unconfirmed`; complaints and care cards carry no badges.
- Dots count equals `unique_asins` / `products_total`; a 12-product run renders 12 dots.
- Nothing else on the page changed colour or font.


**Settled in Phase 4 (built 2026-09-17).** `buildViews` already had ranks per source, so the
rank line (`rankHtml`: `listings #11 · reviews #2`, or `not in listings`) costs nothing and
sits under the counts on every cluster card and theme row. Coverage is `covHtml`: one dot
per product, filled in cobalt when the item appears in it, plus `N/M`, falling back to text
past `COV_MAX_DOTS` (20). Gap badges are features-only and the thresholds moved after one
look at real output: the planned "2× share ratio" flagged a third of every list, including
top-three listing features, so **under-advertised** now needs review mentions ≥ 5, a review
share at least 3× the listing share *and* a listing rank at least 3 places below the review
rank (or no listing mention at all); **unconfirmed** is simply listing mentions ≥ 3 with
review mentions ≤ 2. On olive trees that yields 5 + 3 of 35 clusters (e.g. *Compact Fit for
Corners* 0 listing / 20 review mentions; *Long-Lasting Fresh Appearance* 6 / 0), on ceramic
mugs 8 + 2 of 31 (*Well-Sized Drink Capacity* 2 / 46), on wood frames 3 + 11 of 53 (that run
has few reviews per listing, so many claims are genuinely unconfirmed). The pill's `title`
states the rule and both ranks. Under-advertised clusters also get a small amber dot after
their label in the overview. Expand all / Collapse all now also unfolds and folds the
overview's theme rows. Constants (`GAP_RATIO`, `GAP_RANK_GAP`, `GAP_MIN_REVIEWS`,
`GAP_MIN_LISTINGS`, `GAP_MAX_REVIEWS`) sit above the results JS with the reasoning.

---

## Files

| File | Change |
|---|---|
| `static/index.html` | results CSS (overview panel, search input, jump links, slim line, flash, coverage dots), results markup (bar contents, overview container, lede removed), results JS (`buildViews`, native ordering, overview renderer, search, insights). |
| `UI-DESIGN-IMPROVEMENT.md` | this plan, saved into the repo on approval; "Settled in Phase N" notes appended as phases land. |
| `CLAUDE.md` | after Phase 4, a short paragraph under Phase 7 pointing at the new doc. |

No changes to `app.py`, `pipeline/`, prompts, or data.

## End-to-end verification (after Phase 4)

1. `python app.py`, open `#/g/olive-trees/r/2026-09-11_155256/p/parse-2026-09-16_162551`.
2. First screen with no clicks: slim line, sticky bar, overview with 11 + 8 segmented bars,
   collapsed themes below. No lede, no tiles.
3. Toggle Listings → bars and sections re-rank and re-number; toggle Reviews → likewise;
   All → identical to `clusters.md` order.
4. Type `pot` → filtered overview and sections, marks visible; Escape clears.
5. Click a bar → scroll, open, flash. Hover a chip → sentence; click → panel. Unchanged
   behaviour downstream of the click.
6. Repeat 2–3 on `wooden-serving-bowls` (unthemed) and `wood-picture-frames` (largest).
7. Open an ungrouped parse → the existing empty state still renders, no overview, no
   JS errors in the console.
