# UI-DESIGN-IMPROVEMENT — Two-tier results page with Keywords

## Context

The results view (Phase 8, 2026-09-17) already renders themes, clusters and tags with
sentence-level provenance, a source toggle, a sticky search and an overview bar panel. Three
things stand between it and "grasp the top insights in one look":

1. **Search traps you open.** While a query is active every matching theme is force-opened
   ([index.html:2636](static/index.html#L2636), `:2502`, `:2507`) so a click on a theme
   header toggles state but the render overrides it. The "Expand all" label ignores search.
   Marked hits also break chip layout: `.bub` is `inline-flex; gap:6px`
   ([index.html:265](static/index.html#L265)) and `hilite()` splits the label into text +
   `<mark>` + text, so each fragment becomes its own flex item and the words drift apart
   (`search-tool-bug.png`).
2. **Bars carry a split nobody needs at a glance.** Listing/review segmentation on the
   overview bars adds a legend and a second shade per list; the source toggle already
   answers "who says it".
3. **Search terms and usage keywords are invisible.** They are tagged for every body
   (25–155 search-term mentions, 70–420 usage mentions per group on disk) but
   `pipeline/results.py` drops them (`_register` keeps only `attribution.LISTS`), and the
   view has no section for them. Deferred since Phase 6.

**Goal:** the page reads as two tiers. **Feature Analysis** (overview bars, then Product
Features & Benefits, Complaints, Assembly/Care) and **Keywords** (Search Terms beside Usage
Keywords), each keyword list ranked, filterable, expandable, and traceable on hover. Search
never traps a theme open, and marked chips keep their shape.

**Untouched:** fonts, tokens, colours (no new colour anywhere: keywords use the existing
cobalt accent, which no Feature Analysis chip or bar uses), tagging / clustering / theming
code and prompts, every data file, the groups / run / parse views. Backend change is one
additive `keywords` block in the results response.

## Decisions settled in scoping (2026-09-18)

| Area | Decision |
|---|---|
| Search + themes | Typing a query auto-opens matching themes; a click then collapses one and it stays collapsed. A new or changed query starts fresh (collapses forgotten). |
| Expand / Collapse all | Works during search; label reflects what is actually open. |
| Search scope | The sticky search filters everything on the page: themes, clusters, tags, search terms, usage keywords. |
| Overview bars | One solid colour per list (celadon features, iron complaints). Bars still re-lengthen by the source toggle. Legend dropped; listing/review split stays in the hover tooltip. |
| Major sections | "Feature Analysis" and "Keywords". Mono eyebrow dividers (`01 · FEATURE ANALYSIS`), not collapsible; only the lists inside collapse as today. |
| Jump links | Gain `Search terms N · Usage N`, counts following source and reading `N of M` while filtering. |
| Search terms display | Ranked rows: term · thin cobalt bar · mentions. 10 rows, then `Show all N`. |
| Usage keywords display | Four stacked sub-blocks (Spaces, Placements, Occasions, Used for), cobalt chips sorted by frequency with `×N`, 6 chips each then `+N more`. |
| Colour | Cobalt (`--cobalt` / `--cobalt-wash`), the default `.bub` and accent token. Nothing new. |
| Dedup | Deterministic normalisation: lowercase, strip punctuation except internal hyphens, collapse whitespace, drop leading articles, fold trailing plural `s` (len > 3, not `ss`/`us`/`is`). Display = most frequent original wording. No model call. |
| Source toggle | Applies to Keywords: counts, order, bars follow the source; tooltip shows `N mentions · a from listings · b from reviews · in x of y products`. |
| Keyword interaction | Hover tooltip only (ASIN, title, source, highlighted sentence, source count). No click-through panel. |
| Local filters | One box per column (Search Terms; Usage across all four sub-lists), in the column header, header count reads `N of M` while filtering, hits marked. Combined with the sticky search (both must match). |
| Show-all state | Session-only, like the overview's show-all today. |
| Bugfix scope | Exactly the two named bugs. |

## Working principles

- One view model per render: `buildViews()` grows a `keywords` branch so sections, jump
  links, overview and Keywords all agree on what is visible under the current source and
  query. Every renderer reads from `resultsState.views`.
- Reuse: `hilite`, `plural`, `esc`, `covHtml`, `tipHtml`/`tipShow`/`tipMove`/`tipOff`,
  `.ovcol`/`.oh`/`.note`, `.ovrow .trk`, `.bub`, `.bubs`, `.lnk`, `.blank`, `.sbar .sq`
  input styling, `.eyebrow`, the mono micro-label rule (`.cl .cb h4`).
- Bars are plain `div`s. New CSS is layout only.
- **Caution from memory:** two lines in `static/index.html` contain a literal NUL byte used
  as a Map-key separator; `Edit` cannot match them. If a change touches those lines, patch
  with a short Python script.
- Backend stays computed-on-request; nothing new is written to disk.

---

## Phase 1 — Search bugfixes

**Theme collapse during search.** Add `resultsState.searchClosed = new Set()` (session-only,
cleared inside `setQuery` whenever the query string changes, so a new query re-opens
everything with a hit). Introduce one helper and route every open/closed decision through it:

```js
function themeOpen(key) {                       // themes in the sections
  if (resultsState.expanded.has(key)) return true;
  return resultsState.terms.length > 0 && !resultsState.searchClosed.has(key);
}
```

- `themeHtml` ([index.html:2636](static/index.html#L2636)): `const open = themeOpen(tv.key)`.
- `toggleKey` for theme keys: if `themeOpen(key)` → `expanded.delete(key)` and, while
  searching, `searchClosed.add(key)`; else `expanded.add(key)` and `searchClosed.delete(key)`.
  Cluster keys keep today's plain toggle. Persist only `expanded`.
- `allThemesOpen` ([index.html:2217](static/index.html#L2217)) uses `themeOpen`, so the
  sticky button's label is right mid-search; Collapse all adds every theme key to
  `searchClosed` and removes from `expanded`; Expand all does the reverse.
- Overview theme rows ([index.html:2502](static/index.html#L2502)) get the same rule with
  their own session set (`ovOpen` + `ovSearchClosed`) via the same helper parametrised on
  the two sets; the caret click toggles it the same way. The unthemed "show all while
  searching" at `:2507` stays (matches are few; no collapse is needed there).

**Marked chips.** In `chipHtml` ([index.html:2715](static/index.html#L2715)) wrap the label:
`<span class="lbl">${hilite(...)}</span>` so the label is a single flex item and the count
badge `<i>` the second; `mark` renders inline within it. Grep for every other `.bub` whose
label is passed through `hilite` (the panel's "other tags" chips at ≈`:3158–3172`) and wrap
the same way. No CSS change needed beyond `.bub .lbl{min-width:0}`.

**Verify:** type `nested` on acacia-wood-riser → themes with hits open → click a theme
header → it collapses and stays collapsed → edit the query → it re-opens → Collapse all /
Expand all label and action both correct → the "can be used separately, stepped together,
or nested at angles" chip renders as one wrapped pill with the mark inline.

**Settled in Phase 1 (built 2026-09-18).** The helper landed as a three-function family rather
than the plan's single `themeOpen`, because the overview needs the same rule on its own two
sets and the toggle needs the inverse: `openRule(key, openSet, closedSet)` is the rule,
`themeOpen` / `ovThemeOpen` bind it to `(expanded, searchClosed)` and `(ovOpen, ovSearchClosed)`,
and `toggleThemeOpen(key, openSet, closedSet)` does the add/delete on whichever pair it is
handed. `toggleKey` routes on the `"theme:"` prefix that `themeKey` already writes, so cluster
keys keep the plain toggle and only `expanded` is persisted. Two things the plan did not spell
out, both deliberate: **a theme with no hits counts as open while searching** (`openRule` asks
only whether it was clicked shut), which is invisible because `themeOrder` never renders it but
does mean `allThemesOpen()` is true the instant you type — so the sticky button correctly reads
**Collapse all** mid-search, which is the bug it was meant to fix; and Collapse all mid-search
has to write every key into *both* closed sets or the next render re-opens everything.

For the chips, wrapping the label was enough on its own: `hilite` splits a match into text +
`<mark>` + text, and the `.bub` gap was pushing each fragment apart as a separate flex item.
One `<span class="lbl">` makes the label a single item and the `<i>` count the second, with
`.bub .lbl{min-width:0}` so a long label can still shrink and wrap. Three renderers pass a
label through `hilite` or `esc` into a `.bub`, and all three were wrapped: `chipHtml` (which
also covers preview chips) and the panel's two "other tags" branches. The panel's `.bub plain`
span needed the wrap too, for shape consistency rather than for marks.

**Measured** with the Node harness (page script in a `vm` context, DOM stubs, real
`results.build` output for acacia-wood-riser): 19 of 19 assertions pass. Typing `nested` opens
all 11 themes (2 of them with hits); clicking one leaves `searchClosed` holding exactly that
key, `expanded` empty, and the other themes open; the card renders `aria-expanded="false"`;
changing the query to `nested tray` empties both closed sets and the theme re-opens; Collapse
all closes all 11 and fills both closed sets, Expand all re-opens all 11 and empties them, and
the label flips correctly at every step; an overview caret click takes the open theme rows from
2 to 1. Of 35 rendered chips, every one carries exactly one `.lbl` span and all 3 marked chips
have the `<mark>` inside it — e.g. `<span class="lbl">space-saving <mark>nested</mark>
storage</span>`.

## Phase 2 — Solid overview bars

In `overviewHtml` ([index.html:2478](static/index.html#L2478)) the `bar` helper always emits
one segment, `seg(v.mentions[src], "l")`; delete the `legend` string (`:2518–2519`) and its
placement; keep `tip(v)` unchanged so the split lives on in the tooltip. CSS: remove lines
358–360 (`.r` shade and `.lg` swatches), keep `.ovcol.feat .trk i` / `.ovcol.comp .trk i`,
and rewrite the comment block at `:329–333`. Also update the CLAUDE.md Phase 8 sentence
"segmented listing/review bars" when the phase is settled.

**Verify:** All / Listings / Reviews each re-lengthen bars; no legend; hover still reads
`106 mentions · 40 from listings · 66 from reviews · in 9 of 12 products`.

**Settled in Phase 2 (built 2026-09-18).** `bar` collapsed to
`seg(v.mentions[src], "l")` with the `src === "all"` branch gone; `seg` keeps its two-argument
shape so the diff is one line. The plan named CSS lines 358–360 for deletion; **339–340
(`.ovcol .lg`, `.ovcol .lg i`) went too**, because the legend was their only caller and leaving
the rules behind would contradict deleting it. That is the one deviation, agreed before
building. `tip(v)` is untouched, so the split it removes from the bar is still one hover away.
The comment block now says that in as many words: length is mentions in the current source,
who said it is the source toggle's job, the split lives in the tooltip.

**Measured** with the harness across three groups (acacia-wood-riser, candle-warmer-lamp,
olive-trees) × three sources: every one of the 162 rendered bars has exactly one `<i>`, no
output contains `class="lg"` or `class="r"`, and the longest bar in each of the nine columns is
exactly 100.00 % with the rest scaled to it — 21/11/21 bars for acacia, 23/15/23 for candle
warmer, 19/10/19 for olive trees under all/listings/reviews. Under **All** the top row's title
still reads e.g. `106 mentions · 32 from listings · 74 from reviews · in 12 of 12 products`.
A regex sweep of the stylesheet confirms `.ovcol .lg` and `.trk i.r` are gone while
`.ovcol.feat .trk i` and `.ovcol.comp .trk i` survive. The CLAUDE.md sentence about
"segmented listing/review bars" is corrected in Phase 5, where the task groups the doc edits.

## Phase 3 — `keywords` block in the results API

File: [pipeline/results.py](pipeline/results.py). No `app.py` change (the route is a
passthrough at `app.py:270`).

- Add `_keyword_norm(text) -> str` implementing the dedup rule from the table. Note that
  `grouping.collect` deliberately does **not** fold plurals for tags (`grouping.py:125–127`);
  keywords are different — a shopper's "picture frame" and "picture frames" are one query —
  so the fold applies here only and the docstring says so.
- After the cluster loop and **after** computing `attributed` / `mentions` (so those two
  figures keep meaning "clustered tag mentions"), walk `bodies_ok` once:
  - `search_terms` → refs `kw/search_terms/o_0000…`
  - `usage_keywords[sub]` for `spaces`, `placements`, `occasions`, `used_for` → refs
    `kw/usage/<sub>/o_0000…`
  - each occurrence is `{"body_id", "asin", "body_type": body["type"], "text": raw}` and is
    passed through the existing `_register(...)`, so it gets attribution (the tooltip can
    highlight the sentence) and its body and product land in `bodies` / `products`.
  - Group by `norm`; `display` is the most frequent original wording (ties → first seen);
    items sorted by `-count, display.lower()`.
- Response gains:

```json
"keywords": {
  "search_terms": {"key": "search_terms", "title": "Search Terms",
                   "items": [{"display": "candle warmer lamp", "norm": "...", "count": 24, "occ_ids": ["kw/search_terms/o_0003", "..."]}]},
  "usage": {"key": "usage", "title": "Usage Keywords",
            "groups": [{"key": "spaces", "title": "Spaces", "items": [...]},
                       {"key": "placements", "title": "Placements", "items": [...]},
                       {"key": "occasions", "title": "Occasions", "items": [...]},
                       {"key": "used_for", "title": "Used for", "items": [...]}]}
}
```

Per-source counts are derived in the browser from `d.occurrences[ref].body_type`, exactly
as clusters do, so the payload stays one shape.

**Verify (REPL):** for acacia-wood-riser, `sum(i["count"] for i in kw["search_terms"]["items"])`
equals the raw search-term occurrence count from tagged.json (110); every `occ_id` resolves
in `occurrences`, every `body_id` in `bodies`; `attributed` and `mentions` are unchanged
from before the phase; `python -m pipeline.attribution <slug> <run>` output unchanged.

**Settled in Phase 3 (built 2026-09-18).** `_keyword_norm` is four regexes and a word loop;
the only subtlety is hyphens, where `[^a-z0-9\s-]` keeps them and a second pass drops the ones
that are not between two characters, so `anti-tip` survives and a trailing dash does not. Its
docstring carries the contrast with `grouping.collect` explicitly, because the two functions
sit one import apart and do the opposite thing on purpose. Two guards the plan did not name:
a keyword that normalises to nothing (all punctuation) is bucketed under its own lowercase text
rather than merging every such tag into one row, and `display` resolves ties by first-seen
index, so the ranking is deterministic across runs. The walk is a `collect` / `gather` pair so
search terms and the four usage sub-lists share one code path; each list numbers its refs from
`o_0000` under its own `kw/…` prefix, which cannot collide with a section's ids.

Ordering mattered more than it looked. `attributed` and `mentions` are now computed into
locals **before** the keyword walk and read from those locals in the return, so both keep
meaning "mentions of a clustered tag" even though `occurrences` has roughly doubled by the time
the dict is built. The keyword refs are also registered *after* the cluster loop, which keeps
the browser's first-wins `byBodyTag` map resolving feature/complaint/care strings to their
cluster refs — the source panel is untouched.

**Measured** on three groups. `attributed` / `mentions` identical to a pre-change snapshot
(acacia 366/401, candle warmer 640/721, olive trees 481/520) and `sections` byte-identical
under a sorted-key dump; `python -m pipeline.attribution acacia-wood-riser 2026-09-17_144159`
byte-identical before and after. Mention counts match tagged.json exactly on every group —
acacia **110** search terms and **354** usage, candle warmer 153 and 327, olive trees 124 and
159 — and all 464 / 480 / 283 keyword refs resolve in `occurrences`, with every `body_id` in
`bodies` and every `asin` in `products`. Distinct counts for acacia after the fold are
**81 search terms and 129 usage keywords** (spaces 13, placements 27, occasions 9, used_for 80).
The plan's original "87 / 135" were the *pre-fold* figures, counted before the dedup rule in
the decisions table was applied; the post-fold numbers are what the page shows and the
paragraphs above now say so.

## Phase 4 — Two tiers and the Keywords section

**Markup** ([index.html:561–605](static/index.html#L561-L605)). Inside `#view-results`, keep
`#res-sum`, `#res-bar` as they are, then:

```html
<div class="mdiv" id="res-h1"><span class="eyebrow">01 &middot; Feature Analysis</span></div>
<div class="ov" id="res-ov" hidden></div>
<div id="res-body"></div>
<div class="mdiv" id="res-h2"><span class="eyebrow">02 &middot; Keywords</span></div>
<div class="kw" id="res-kw" hidden>
  <section class="kwcol" id="sec-search_terms">
    <div class="oh"><h2>Search Terms</h2><span class="note" id="kw-st-note"></span></div>
    <div class="sq"><input type="search" id="kw-st-q" placeholder="Filter search terms"><button class="x" hidden>&times;</button></div>
    <div id="kw-st-list"></div>
  </section>
  <section class="kwcol" id="sec-usage"> … same shell, ids kw-us-note / kw-us-q / kw-us-list … </section>
</div>
```

The shell is static so the filter inputs keep focus and caret while the lists re-render;
only `#kw-*-note` and `#kw-*-list` are rewritten by `renderResultsBody`. Dividers are hidden
until results load, like `#res-ov`.

**CSS (layout only).** `.mdiv{display:flex;align-items:center;gap:12px;margin:6px 0 16px}
.mdiv .eyebrow{margin:0} .mdiv::after{content:"";flex:1;border-top:1px solid var(--rule)}`,
`#res-h2{margin-top:34px}`. `.kw` reuses the `.ov` grid rule (`repeat(2,minmax(0,1fr))`) and
joins the 640 px media query at `:419`. `.kwcol` = `.ovcol` box; `.kwcol .sq` = `.sbar .sq`
input rules with `max-width:none; margin:0 0 10px`. Search-term rows: `.kwrow{display:grid;
grid-template-columns:minmax(0,1fr) minmax(80px,1fr) 36px}` copying `.ovrow`'s padding,
hover and `.lb/.trk/.val` rules, with `.kwcol .trk i{background:var(--cobalt)}`. Usage
sub-blocks: `.kwsub h4` = the mono micro-label rule, with the count pushed right in `.note`
style. Chips: plain `.bub` (already cobalt) with `data-kwoccs`.

**View model.** `buildViews()` adds `resultsState.views.keywords`:
- for every item: `mentions{all,listing,review}` and `asins{…}` from its `occ_ids`;
  `present(src)`; `match` = sticky terms **and** the column's local terms all found in
  `display`; order by `mentions[src]` desc, then server order.
- `search_terms`: `{order, count, total, mentions}`; `usage`: the same per group plus
  section totals (distinct keywords, mentions), so jump links and header notes are one
  lookup.
- `resultsState.kwQuery = {search_terms: "", usage: ""}` and `resultsState.kwAll = new Set()`
  (`"search_terms"`, `"usage:spaces"`, …), both session-only. Local inputs debounce 120 ms
  like `#res-q`; Escape clears; `.x` shows when non-empty.

**Renderers.** `keywordsHtml()` writes the two lists and notes:
- Search Terms: `KW_TOP_TERMS = 10` rows, each `<div class="kwrow" data-kwoccs="…" title="…">
  <span class="lb">${hilite(display, allTerms)}</span><div class="trk"><i style="width:%"></i></div>
  <span class="val">N</span></div>`; bar scale = top visible item; then
  `<button class="lnk more" data-kwall="search_terms">Show all 81</button>` / `Show top 10`.
- Usage: four `.kwsub` blocks, `KW_TOP_USAGE = 6` chips each, `+N more` toggles that block's
  key in `kwAll`. A group with nothing under the current source/filter is omitted; if all
  four are, the column shows the `.blank` state.
- Header notes: `81 terms · 110 mentions`, or `12 of 81 terms` while either filter is active.
- Empty states mirror `nothingHtml`: "Nothing matches “…”" with a Clear-the-filter link
  (clears the local box only) / "Nothing from reviews" / "No search terms came out of this parse".

**Sticky bar and slim line.** `#res-jump` appends `Search terms <b>N</b>` and
`Usage <b>N</b>` (`data-sec="search_terms"` / `"usage"`, so the existing scroll handler
finds `#sec-search_terms` / `#sec-usage`); `N` follows the source and reads `N of M`
during a sticky search. `#res-sum` appends `81 search terms · 129 usage keywords`.

**Hover.** Extend the document-wide `mouseover` / `mousemove` / `mouseout` selectors
([index.html:3085–3100](static/index.html#L3085-L3100)) to `.bub[data-occs], [data-kwoccs]`.
`tipHtml(occs, {clickable})` gains a second argument: for keywords the footers become
`No exact sentence found` (no "click for the full text") and `1 of N sources` (no "click to
see all"), plus `in x of y products`. The document-wide click handler keeps matching only
`.bub[data-occs]`, so keyword rows and chips open no panel (E1). Flipping that later is one
attribute rename.

**Verify:** open acacia-wood-riser → two eyebrow dividers; overview and three lists under
the first, two columns under the second; top search term first with the longest bar; Usage
shows four blocks of ≤ 6 chips; `+N more` expands one block; `Show all 81` expands the rows
and the button reads `Show top 10`; type in the Search Terms box → rows filter, hit marked,
note reads `12 of 81 terms`, caret stays in the box; sticky search `kitchen` narrows both
columns and the jump links read `N of M`; Reviews toggle re-ranks the keywords and hides
listing-only ones; hover a row → tooltip with ASIN, title, highlighted sentence, no "click"
copy; click does nothing; 600 px width stacks the columns.

**Settled in Phase 4 (built 2026-09-18).** The view model went in as `buildKeywordViews()`
hung off `resultsState.views.keywords` — a property on the Map, which cannot collide with its
entries because `sectionView` only ever calls `.get()`. Each item view carries `mentions`,
`asins` **and `refs`, all three per source**, and `kwAttrs` hands the hover the refs for the
*current* source: under **Reviews** a hover opens a review rather than the bullet that happened
to be first, which is what "counts, order, bars follow the source" has to mean once the tooltip
shows a sentence. `listView` / `summed` are shared by the search-term column and all four usage
sub-lists, so a sub-list, a column and the jump links are one lookup apart.

Three decisions the plan left open. **The native `title` on `.kwrow` was dropped** (the one
agreed deviation): the row already has the `#tip` hover card, and a browser tooltip would stack
on top of it after a delay. **The hover payload travels in `data-kwstats`**, because the
document-wide handler has only the element — `tipHtml(occs, opts)` gained `opts.clickable` to
strip the two "click …" halves and `opts.stats` to append that line, and the existing chip call
site passes nothing, so its output is byte-identical. **`+N more` flips to `Show top 6`** once a
usage block is open, matching the search-term column rather than stranding an expanded block
with no way back. Keyword chips are `<span class="bub">`, not `<button>`: there is nothing to
press, and the document-wide click handler still matches `.bub[data-occs]` only, so neither a
row nor a chip can open the panel. Flipping that later is still one attribute rename.

**Measured** with the harness across acacia-wood-riser, candle-warmer-lamp and olive-trees —
105 assertions, all passing. Ten rows by default with the top bar at exactly 100 % and the rest
descending; four usage blocks of ≤ 6 chips; `Show all 81` expands to 81 rows and flips to
`Show top 10`; `+N more` on spaces expands that block alone (13 chips) and leaves the other
three at 6. All 242 / 362 / 283 rendered `data-kwoccs` refs resolve in `occurrences`, and no
`data-occs` appears anywhere inside `#res-kw`. Under **Reviews** every rendered ref is a review
(24) and under **Listings** every one is a listing (17); returning to **All** restores the
original top term.

**In the browser** (Playwright against a live `python app.py`, no console errors at either
width): the local filter `wood` takes the note to `42 OF 81 TERMS`, marks every hit, keeps the
caret in the box and moves the jump link to `SEARCH TERMS 42 OF 81` while the usage column's
note stays `129 KEYWORDS · 354 MENTIONS`; the sticky search narrows both columns at once.
Hovering the top row gives `B0GTYTMYQB · REVIEW · ★★★★★ · 2026-07-24`, the product title, the
highlighted sentence *"These are nice **wood display risers** that can be set up in a variety of
configurations."*, then `1 of 4 sources` and `4 mentions · 1 from listings · 3 from reviews ·
in 2 of 12 products` — no "click" copy anywhere. Clicking the row, and clicking a usage chip,
both leave `#panel` hidden. At 600 px the two columns stack. Screenshots:
`shot-1280-full.png`, `shot-1280-keywords.png`, `shot-1280-search.png`,
`shot-1280-kwfilter.png`, `shot-1280-kwhover.png`, `shot-1280-markedchip.png`,
`shot-600-full.png`, `shot-600-keywords.png`, `shot-600-search.png`.

**One trap worth recording:** a `python app.py` left running from before Phase 3 keeps serving
the old `results.build`, so the page renders with no Keywords tier and no error — the guard
(`if (!d.keywords)`) doing exactly its job. Restart the server after a backend change, or check
`curl …/results | grep keywords` before concluding the front end is broken.

## Phase 5 — Documentation and end-to-end check

- Save this file as `UI-DESIGN-IMPROVEMENT.md`; append a "Settled in Phase N" paragraph
  after each phase lands, in the house style.
- Add a short "Phase 10 — Two-tier results page and Keywords (built …)" note to `CLAUDE.md`
  pointing at the doc, and correct the Phase 8 wording about segmented bars.
- Refresh the memory note `index-html-nul-byte.md` only if the NUL lines moved.

**Settled in Phase 5 (built 2026-09-18).** `CLAUDE.md` gained a **Phase 10 — Two-tier results
page and Keywords** section between Phase 9 and the Phase 6 deferred list, summarising the two
bugfixes, the solid bars, the additive `keywords` block and the hover-only Keywords tier, and
pointing here for the spec. "Search terms and usage keywords" is struck from the Phase 6
deferred list, which now holds only the deeper grouping rules. The Phase 8 sentence about "an
overview panel of segmented listing/review bars" now describes one bar per theme in the list's
colour and says in the same breath that the segmentation was removed in Phase 10 and the split
moved to the tooltip — the history is worth keeping, since the screenshots in that phase's notes
still show two shades. The pre-fold figures **87 / 135** were corrected to the post-fold
**81 / 129** in the six places this document used them to describe what the built page shows.

The NUL-byte lines did not move in a way that changes the memory note: they are still exactly
two, still the `byBodyTag` key separators, now at 2208 and 3519 (they were 2134 and 3166).
`index-html-nul-byte.md` names the symptom rather than the line numbers, so it needed no edit.
Both were worked around rather than patched: Phase 1's panel edit starts one line below the
second NUL, so `Edit` never had to match it and no Python patch was needed all build.

**End-to-end, on the final tree.** All three harnesses re-run green after the documentation
edits (19 + 35 + 105 assertions), `pipeline.results` re-checked against the pre-change snapshots
for three groups, and `python -m pipeline.attribution acacia-wood-riser 2026-09-17_144159`
byte-identical to its pre-Phase-3 output. Nothing was written to `data/`, and no commit was made.

## Files

| File | Change |
|---|---|
| `static/index.html` | Phase 1 search-state helper, `toggleKey`, `allThemesOpen`, overview open rule, `.lbl` wrapper in chip renderers; Phase 2 `bar`/legend/CSS; Phase 4 markup shell, layout CSS, `buildViews` keywords branch, `keywordsHtml`, jump links, slim line, hover wiring, `tipHtml` option. |
| `pipeline/results.py` | Phase 3 `_keyword_norm`, keyword occurrence walk through `_register`, `keywords` block in the return. |
| `UI-DESIGN-IMPROVEMENT.md` | This plan, then per-phase settled notes. |
| `CLAUDE.md` | One Phase 10 pointer and the Phase 8 bar wording. |

No changes to `app.py`, `pipeline/tagging.py`, `grouping.py`, `themes.py`, `attribution.py`,
`partition.py`, prompts, or any data file.

## End-to-end verification (after Phase 4)

1. `python app.py`, open acacia-wood-riser (12 ASINs, 81 distinct search terms, 129 usage
   keywords) and candle-warmer-lamp (the group in the screenshot).
2. Search `nested`: themes with hits open; collapse one; it stays; change the query; it
   re-opens; Collapse all closes every theme and the label flips.
3. Marked chips keep pill shape; compare with `search-tool-bug.png`.
4. Overview bars are solid; toggle All / Listings / Reviews; hover shows the split.
5. Keywords: counts on the header, the jump links and the slim line agree with a REPL count
   over tagged.json (after normalisation, distinct counts; before, mention counts).
6. Local filter, sticky search and source toggle compose correctly (each alone, then all
   three together); clearing each restores the previous state.
7. Hover a search term and a usage chip: right ASIN and sentence; click opens nothing.
8. Repeat the Phase 8 harness: extract the page script, run `buildViews` / `renderResultsBody`
   / `keywordsHtml` in Node with DOM stubs against `pipeline.results.build(...)` for three
   groups; Playwright screenshots at 1280 px and 600 px.
9. Reload: theme expansion still remembered; keyword show-all and local filters reset.
