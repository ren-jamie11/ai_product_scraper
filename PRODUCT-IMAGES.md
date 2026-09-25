# Product images in the results view — hover image, panel gallery, zoom view

## Context

Hovering or clicking a tag in the results view shows the product only as a 56 px (hover card)
or 96 px (side panel) thumbnail, which is too small to see which colours, materials or
details a review is talking about. This change makes the product image large and clear right
under the cited sentence, lets you step through every gallery image for that ASIN, and adds an
Amazon-style full-screen zoom view. Insights like "bright, playful colors" then come with the
product images they describe.

The images already exist: `extracted.json` holds `main_image` and up to 8 gallery `images` per
ASIN, and the main image is never repeated among them. Measured over 166 products: 4–9 images
each. Only the page never receives them.

## Decisions (settled in scoping, 2026-09-25)

| # | Decision |
|---|---|
| A1 | Hover card: **main image only**, large, below the sentence, plus a foot line `1 / N images · click to browse` (only when N > 1 and the chip is clickable). There are no arrows because the card takes no clicks. Hover behaviour is otherwise unchanged. |
| A2 | Hover card **keeps** its 56 px thumbnail header. |
| A3 | Image frame = full card width, always. Height follows the image's aspect ratio up to a cap (≈55vh). A taller image is scaled down (`object-fit:contain`) and centred on white. It is never cropped or distorted. |
| A4 | Keyword hovers (Search Terms rows, Usage chips) get the same image, since they share `tipHtml`. |
| B1 | Panel viewer sits **directly below `.ptext`**, full text width, above `.pother`. |
| B2 | Controls: ‹ › buttons over the image's left and right edges, plus a small `3 / 7` counter. Clicking the image opens the zoom view. No thumbnail row in the panel. |
| B3 | Paging sources: **keep the image index if the next source is the same ASIN**, otherwise reset to the main image. |
| B4 | Panel header keeps its 96 px thumbnail (not clickable). |
| C1 | Zoom view is Amazon-style: white full-screen overlay, large image, ‹ ›, a thumbnail strip on the right with the product title and `n / N`, +/− zoom buttons, × to close. Esc closes; ← / → step images. |
| C2 | Zoom: the wheel zooms toward the cursor (1×–4×), +/− step it, drag pans when zoomed, double-click toggles 1× ↔ 2.5×. Changing image resets to 1×. |
| C3 | The zoom view loads the **full-size original**: Amazon's size code is stripped from the URL (`._AC_SL1200_.jpg` → `.jpg`). If that fails to load, it falls back to the stored URL. |
| C4 | The zoom view opens **only from the panel image**. |
| D1 | `pipeline/results.py` adds one field `images` to each product in the payload. This is the only backend change. |
| D2 | The 8-image cap in `normalize.py` stays. |
| D3 | Nothing else changes: Extract view, clusters, themes, keywords (apart from their hover image), pipeline logic. |

## Implementation

### 1. Backend — `pipeline/results.py` (one field)

In `_register`'s `used_products[asin] = {...}` ([results.py:205-215](pipeline/results.py#L205-L215)),
add `"images": _gallery(p)`. That is a small helper next to it: `[main_image] + images`, dropping
empties and dropping duplicates by Amazon image id (the filename up to its first `.`). No disk
writes. Manual or removed products with no images give `[]`.

### 2. Frontend — `static/index.html` only

**Shared helpers** (next to `thumbHtml`, ~[index.html:3425](static/index.html#L3425)):
- `gallery(p)` → `p.images && p.images.length ? p.images : (p.main_image ? [p.main_image] : [])`.
  Keeps an old server or payload working with the main image only.
- `fullSize(url)` → strips `/\._[^/]*_(?=\.(jpe?g|png|webp|gif)$)/i`.
- `bigImgHtml(src, cls)`: a `.bigimg` frame (full width, white background, 3 px radius, and
  `aspect-ratio:1` reserved until load so the card doesn't jump) holding an `<img>` with
  `width:100%; height:auto; max-height:<cap>; object-fit:contain`. On load, clear the reserved
  ratio. On error, show the existing `.noimg` placeholder.

**Hover card** (`tipHtml`, [index.html:3444](static/index.html#L3444)):
- After `.sent` and the existing foot lines, append the big image of `gallery(p)[0]`. Add the
  `1 / N images · click to browse` foot only when N > 1 and `clickable`. Keyword hovers
  (`clickable:false`) show the image without that foot.
- When there is an image, the card is set to a fixed width equal to today's max
  (`min(420px, calc(100vw - 24px))`) so the image and the text share one width. `max-width` is
  unchanged. Image cap is `min(55vh, …)`.
- `tipShow` / `tipMove`: after the image loads, call `tipMove` again with the last pointer
  position so the taller card is re-clamped inside the viewport. The existing clamp logic
  already handles `y`.

**Panel viewer** (`renderPanel`, [index.html:3546](static/index.html#L3546)):
- `panelState` gains `img` (index) and `imgAsin`. When `renderPanel` runs for a source whose ASIN
  differs from `imgAsin`, reset `img = 0` (B3).
- Insert a `.pgal` block between `.ptext` and `.pother`: the big image (cap ≈70vh, since the
  panel scrolls), ‹ › buttons overlaid mid-height on the left and right edges (styled like
  `.pager button`, hidden when N = 1, disabled at the ends), and a mono `3 / 7` counter. There is
  no block when there are no images.
- The arrows change only the image (`panelState.img ± 1`). Re-render only `.pgal`, not the whole
  panel, so scroll position holds. Clicking the image calls `openZoom(gallery(p), panelState.img, p.title)`.
- ⚠ `renderPanel` contains a literal NUL byte (the `byBodyTag` lookup,
  [index.html:3583](static/index.html#L3583)). Edits touching that region must be done with a Python
  `str.replace` (`newline=""`, assert one match), not the Edit tool.

**Zoom view** (new, reusing the tokens):
- Markup: `<div id="zoom" class="zveil" hidden>` next to `#panel`, z-index above the panel (90).
  It has a white ground, a stage filling the left area with `<img>` transformed by
  `translate(x,y) scale(s)`, ‹ › at the stage edges, and a right rail (~220 px) holding the
  product title, `n / N`, a thumbnail grid (active one outlined in `--cobalt`), and +/−. There is
  a × at the top right. At ≤ 640 px the rail moves below the stage as a horizontal strip.
- State: `{ urls, at, s, x, y }`. Wheel means zoom toward the cursor, clamped to 1–4. +/− step
  ×1.5. Drag with pointer events pans when s > 1, clamped so the image can't leave the stage.
  Double-click toggles 1 ↔ 2.5 at the cursor. `cursor: zoom-in / grab / grabbing`.
- The image `src` is `fullSize(url)`, with `onerror` falling back once to `url`.
- Keys: add a first branch to the global Escape handler
  ([index.html:3737](static/index.html#L3737)) so Esc closes the zoom view before the panel.
  ← / → are handled only while the zoom view is open. Clicking the white area outside the image
  closes it. The panel stays open underneath.

## Critical files

- [static/index.html](static/index.html): CSS near `#tip` / `#panel` (~lines 430–476), `tipHtml`,
  `tipShow`/`tipMove`, `renderPanel`, `openPanel`, the Escape handler, and new zoom markup, CSS and JS.
- [pipeline/results.py](pipeline/results.py): the one `images` field.

## Delivery

On approval, save this plan at the repo root as `PRODUCT-IMAGES.md`. Then an Opus subagent
implements it end to end without stopping and appends a "Settled (built 2026-09-25)" note with
anything measured. After the `results.py` change, **restart `python app.py`**, because a stale
server serves the payload without `images`.

## Verification

1. `python -c` check: `pipeline.results.build(...)` for 2–3 groups gives every product an `images`
   list with the main image first, no duplicate ids, and a length of 1 + stored gallery count.
2. Page-script harness (the Phase 8 approach: extract the `<script>`, run it in a Node `vm` with
   DOM stubs against real `results.build` output). Check that `tipHtml` includes the big image and
   the `/ N images` foot only when clickable with N > 1, that keyword tips have the image and no
   foot, and that `renderPanel` places `.pgal` after `.ptext` and resets or keeps the index per B3.
3. Playwright with the restarted app at 1280 px and 600 px:
   - Hover a feature chip: the image sits under the sentence, the same width as the text, the
     card stays inside the viewport. Hover a keyword row: image present.
   - Portrait image (find one with natural height > width): letterboxed at the cap, not cropped.
   - Click a chip, then › / ‹ in the panel: the counter updates, the panel doesn't scroll. Page to
     a source on the same ASIN: the image is kept. Different ASIN: it resets.
   - Click the panel image: the zoom view opens on the same image at full-size resolution. Check
     wheel zoom, +/−, drag pan, double-click, thumbnail click and ← / →. Esc closes the zoom view
     and leaves the panel open, then a second Esc closes the panel.
4. Regression: clusters, themes, search, source toggle and the keywords tier render as before.
   `git diff --stat` touches only `static/index.html`, `pipeline/results.py` and the new
   `PRODUCT-IMAGES.md`.

## Settled (built 2026-09-25)

Built as specified: `images` on every product in the results payload (`results._gallery`: main
first, deduped by Amazon image id), and in `static/index.html` the big image on the hover card,
the `.pgal` viewer in the panel, and the `#zoom` view. No other view or pipeline file changed.

- **Measured over 13 grouped parses (121 products): 4–9 images per product**, main first, no
  duplicate ids. One product (wood-picture-frames `B0FCY6QD9G`) comes back with 4 images, not the
  1 + 6 stored: Rainforest returned the same three gallery images twice, and the id dedup
  collapses them. That is the dedup working, so "length = 1 + stored count" holds everywhere
  except where the stored gallery repeats itself.
- **Hover card.** Card order: header, sentence, the existing feet, the image, then
  `1 / N images · click to browse` (clickable tips with N > 1 only). `#tip.wide` fixes the width at
  `min(420px, calc(100vw - 24px))` whenever there's an image; `--cap` is 55vh. A cached image is
  settled the moment it's inserted (`settleImgs`), so a repeat hover doesn't flash the reserved
  square; an uncached one re-clamps the card with the last pointer position on load.
- **Panel.** Arrows re-render `#pgal` only and keep focus on an arrow. The image cap is 70vh.
  `1 / 1` is still shown when there's a single image; the arrows are not.
- **Zoom view.** Matches the spec. Three details it didn't settle: the pan clamp keeps an axis
  centred until the zoomed image outgrows the stage on it, so at low zoom the point under the
  cursor can drift on that axis (it holds exactly once the image overflows); a spinner shows
  while the full-size original loads, and "image didn't load" shows if the stored URL fails too;
  the stage arrows are disabled at the ends, like the panel's.
- **One addition:** closing the zoom view leaves the panel on the image the zoom view was on,
  since stepping through the zoom view and landing back on the old image felt broken.
- **Verified.** Node `vm` harness against real `results.build` output for olive-trees,
  colorful-picture-frames and dishwasher-rack: tip image and foot rules over all 3,102 occurrences,
  keyword tips image with no foot, `.pgal` between `.ptext` and `.pother`, B3 keep and reset,
  `fullSize`, `gallery` fallback. Playwright at 1280 × 800 and 600 × 900, 47/47 checks each: the
  portrait main image on candle-warmer-lamp `B0CTJGJL2T` (1969 × 2560) letterboxed at 55vh inside
  the viewport, image width = text width (396 px), panel scroll held while paging images, same-ASIN
  keep / new-ASIN reset through the pager, the zoom view on the same image at the original
  resolution (`._AC_SL1500_` 1500 px → 1600 px), wheel toward the cursor, 4× clamp, ±1.5 steps,
  drag clamped, double-click 1 ↔ 2.5, thumbnails, ← / →, Esc closes the zoom view then the panel,
  a click on the white closes the zoom view only, and clusters, themes and keywords still render with
  no page errors. The fallback was checked by aborting the full-size request (it fell back to the
  stored URL), and by aborting both (broken state shown).
- The two NUL bytes in `index.html` are untouched (none of the edits touch those lines).

**Restart `python app.py`** after pulling this: a server started before the `results.py` change
serves products without `images`, and the page falls back to the main image only, with no gallery.


---

## Round 2: click-to-pin card (scoped 2026-09-25)

### Context

Round 1 (built 2026-09-25, spec and Settled note in `PRODUCT-IMAGES.md`) put the big product
image in every hover card and a gallery in the side panel. In use, the image on hover is noise:
many tags (`packaging`, `uv-resistant`) need no visual, and a 400 px image appears on every
mouse pass. Round 2 separates the two:

- **Hover** is back to the original compact card.
- **Click** pins that same card in place and expands it to show the product image with ‹ ›,
  "read more", source paging and links.
- The side panel is removed, because the pinned card now does its job.

The zoom view, the `images` field in `results.py`, and the `.bigimg` frame from round 1 are kept
and reused.

### Decisions (settled in scoping, 2026-09-25)

| # | Decision |
|---|---|
| A1 | On click the card pins **beside the chip**, at the hover position, and is re-clamped into the viewport as it grows. |
| A2 | The pinned card is `position:fixed`. **Any page scroll closes it.** Scrolling inside the card scrolls it only when its content overflows, otherwise nothing happens. The page never scrolls from a wheel over the card. |
| A3 | Closes on: click outside, Esc (zoom view first if open), a small × at the top right. |
| A4 | **Locked while pinned.** Other chips show no hover and can't be clicked through, because a light veil covers the page. A click on the veil (on a chip or anywhere else) just closes the card. |
| A5 | **Light dim** behind the pinned card (the old `.pveil` look). |
| B1 | Layout, top to bottom: header (56 px thumb, `ASIN · REVIEW · ★★★★★ · date`, title, ×) → review title → sentence + `read more` → `1 / 7 images` → big image with ‹ › over its edges → a bottom-right pager `← 1 of 3 sources →`. |
| B2 | `read more` expands **in place** to the full body text with the cited sentence still highlighted, plus `show less`. It appears only when the full text differs from what is shown. When the card gets taller than the viewport it scrolls inside (`max-height: 100vh − 16px`). |
| B3 | Carried over from the panel: **review title only**, in bold above the text, for reviews that have one. Price, rating and brand pills, the tag name or cluster heading, and "other tags from this source" are dropped. |
| B4 | Hover card footer shows **facts only**: `1 of 3 sources` and `No exact sentence found`, with no "click to …" wording. Round 1's hover image and `1 / N images` foot are removed. |
| C1 | Stepping sources keeps the image index if the source is the same ASIN, and resets to the main image otherwise. An open `read more` collapses on every source step. |
| C2 | Keys while pinned: **Esc only.** ← / → stay with the zoom view. |
| C3 | Search-term rows and usage chips **keep the original hover-only card**: the sentence and stats foot, no image, not clickable. |
| — | ASIN and product title in the pinned card link to `p.link` (new tab, `rel=noopener`). The big image opens the existing zoom view, and closing the zoom view leaves the card on the image last viewed there. |
| D1 | The side panel is **deleted**: markup, CSS and JS. |
| D2 | Only `static/index.html` changes. The zoom view internals, `results.py`, clusters, themes, keyword logic and the Extract view are untouched. |

### Implementation (`static/index.html` only)

**Hover card back to original.** In `tipHtml` (~L3562), remove the big image, the `/ N images`
foot and both "click to …" suffixes. The `clickable` option then has no job and goes. Keyword
hovers still pass `stats`. `tipShow` drops the `wide` toggle and `settleImgs`.

**Pinned card: one `#tip` element, two states.** Add `pinState = { occs, at, img, imgAsin, more, x, y }`.
- `pinCard(el, ev)` is called from the existing document click handler, which still matches
  `.bub[data-occs]` only, so keywords stay unclickable. It records the anchor (the click point),
  fills `#tip` with `cardHtml()`, adds `.pin` (`pointer-events:auto`, fixed width
  `min(420px, calc(100vw − 24px))`, `max-height: calc(100vh − 16px)`, `overflow:auto`,
  `overscroll-behavior:contain`, z-index 85), shows `#pveil` repurposed as the card veil (84),
  and clamps the position with the same logic as `tipMove`, using the anchor.
- `cardHtml()` builds on the `tipHtml` pieces (`thumbHtml`, `sourceLabel`, `markedSentence`,
  `markedBody`, `bigImgHtml`, `gallery`): ASIN and title wrapped in `<a href=p.link>` (plain text
  when there is no link), review title, sentence or full text (`.more`, using `white-space:pre-wrap`
  when expanded) with a `read more` / `show less` text button, `1 / N images` (only when N > 1),
  the `.bigimg` (cap 55vh, `cursor:zoom-in`) with `.gnav` ‹ › when N > 1, and the sources pager
  when there is more than one source. The pager is styled like `.pager` in a dark variant that
  suits the ink ground.
- Re-render on every state change (image step, source step, read more) and re-clamp after, so the
  card stays on screen as it grows or shrinks. `bigImgDone` re-clamps against the pin anchor when
  pinned (today it uses `tipLast`).
- `unpin()` removes `.pin`, hides the veil, clears `pinState`, and calls `closeZoom()`.
- Close triggers: veil click, ×, Esc, and `window` `scroll`. The card's own overflow scroll
  doesn't bubble to window. A `wheel` listener on the card calls `preventDefault` when the card
  can't scroll further in that direction, so a wheel over it never scrolls the page and never
  closes it.
- While pinned, the `mouseover`/`mousemove`/`mouseout` hover handlers return early, so the card
  isn't re-rendered or hidden under the cursor.

**Zoom wiring.** The card image calls `openZoom(gallery(p), pinState.img, p.title)`. In `closeZoom`,
replace the panel sync (`panelState` / `renderPgal`, ~L3839) with the same sync for `pinState`
(set `img`, re-render the card). The zoom view (z 90) sits above the veil and card. The Esc
handler order becomes zoom → card → the existing modal branches.

**Delete the side panel:** `<aside id="panel">`, the `#panel` / `.ph` / `.pager` (if nothing
else uses it; check first) / `.psrc` / `.ptext` / `.pother` / `.pgal` / `.gcount` CSS and the
640 px and reduced-motion rules for `#panel`, and `openPanel` / `closePanel` / `renderPanel` /
`pgalHtml` / `panelProduct` / `renderPgal` / `wirePgal`. Other references to update:
- `showView` (~L1051): `closePanel()` becomes `unpin()`.
- The `/` shortcut guard (~L2683): `!$("panel").hidden` becomes `pinState`.
- Stale comments that mention the panel (~L2854, 2859, 3559, 3575, 3624).
- `byBodyTag` (~L2350–2356, and `resultsState` at ~L2369) is used only by the panel's "other
  tags" chips, so it goes too.
- ⚠ The `byBodyTag` key and the `renderPanel` lookup hold a **literal NUL byte**. Remove those
  lines with a Python `str.replace` (`encoding="utf-8", newline=""`, assert exactly one match
  each), never with the Edit tool. Afterwards, `grep -c $'\x00'` should show **0** NUL bytes left,
  because both users are gone. Say so in the Settled note.

### Verification

1. Node vm harness (page script and DOM stubs against real `pipeline.results.build` output for
   three groups):
   - `tipHtml` has no `.bigimg`, no "click to" and no "images" text, and keyword tips keep their
     stats foot.
   - `cardHtml` layout order matches B1.
   - `read more` appears only when the body is longer than what's shown.
   - The C1 keep and reset rule holds.
   - No reference to `panel`, `renderPanel` or `byBodyTag` remains.
2. Playwright on a fresh server (spare port), at 1280 and 600 px:
   - **Hover:** compact original card with no image. Keyword hover unchanged.
   - **Pin:** click a chip; the card grows beside the chip, stays inside the viewport and doesn't
     follow the mouse. The veil dims the page.
   - **Images:** image ‹ › and the counter work.
   - **Sources:** ← → step sources; the image is kept on the same ASIN and reset on a different
     one, and read more collapses.
   - **Read more:** it expands and shows less. A long review makes the card scroll inside
     without scrolling the page.
   - **Links:** the ASIN and title open the Amazon URL (check the href and target).
   - **Zoom:** clicking the image opens the zoom view, and closing it syncs the card's image.
   - **Esc:** Esc closes the zoom view, then the card.
   - **Close:** a click on the veil or on another chip closes the card without pinning. A page
     scroll closes the card. The × closes it.
   - **Keywords:** clicking one does nothing.
3. Regression: clusters, themes, search (`/` shortcut), source toggle and keywords render with no
   page errors. `git diff --stat` shows only `static/index.html` (plus `PRODUCT-IMAGES.md`).

### Delivery

Append a `## Round 2: click-to-pin card (scoped 2026-09-25)` section to `PRODUCT-IMAGES.md`
with this decisions table. An Opus subagent then builds it end to end without stopping,
appends `## Settled: round 2 (built 2026-09-25)`, and reports back. No commit.

## Settled: round 2 (built 2026-09-25)

Built as specified, in `static/index.html` only. The hover card is back to the compact original:
no image, and a facts-only footer (`1 of N sources`, `No exact sentence found`, keyword stats).
Clicking a `.bub[data-occs]` chip pins the same `#tip` (`.pin`) beside the click point over `#pveil`.
The pinned card shows the review title, the sentence with read more / show less, `n / N images`,
the big image with ‹ ›, and the `← n of N sources →` pager. The ASIN and title link to `p.link`
(`_blank`, `noopener`). The side panel is gone: its markup, CSS (including the 640 px and
reduced-motion rules and the now-orphaned `.bub.plain`), `openPanel` / `closePanel` /
`renderPanel` / `pgalHtml` / `panelProduct` / `renderPgal` / `wirePgal` and `byBodyTag`. `.pager`,
`.ptext`, `.pother`, `.psrc`, `.ph` and `.gcount` were used only by the panel, so they went too.
`.rv`, `.note-line` and `.x` are still used elsewhere and were kept. `.gnav`, `.bigimg`,
`markedBody`, `bigImgHtml`, `gallery` and the zoom view were kept and reused.

- **NUL bytes: 0 left** in `index.html`. Both were removed by a Python `str.replace` (the
  `byBodyTag` map, and the whole panel section holding the lookup), with each match asserted.
- **Shared pieces.** `sentenceHtml` (hover and pin) and `hasMore` (read more only when the
  whitespace-flattened full text differs from what is shown). `placeAt(x, y)` is the old
  `tipMove` clamp, used by the hover card with the pointer and by the pinned card with its anchor.
  `tipLast` is gone, and `bigImgDone` re-places the pinned card. `tipOff` is a no-op while pinned,
  because `openZoom` calls it.
- **Interaction details the spec left open.** A keyboard-activated chip (`detail === 0`) anchors
  on the chip's rect. Focus goes to × on pin and back to the chip on close. Arrows and the pager
  keep focus on the pressed control. Show less resets the card's scroll to the top, while a source
  step keeps it, so the pager stays under the pointer. On close the card hides before `.pin` is
  removed, so it doesn't reflow to hover width while fading out. `resize` re-places it.
- **Deviations.**
  - The pinned card also shows `No exact sentence found` under the text when there is no
    confident match. B1 doesn't list it, but otherwise you can't tell why nothing is highlighted.
  - The wheel guard (`containWheel`) is also on `#zoom`. A wheel over the zoom rail used to scroll
    the page under it, and now that would close the card and the zoom view together. The zoom
    view's internals are untouched.
  - `#tip` got `role="dialog" aria-label="Tag source"`, and `aria-hidden` is toggled on pin.
  - Cluster chips still carry `data-cluster`, which nothing reads now. It was left alone, since
    the cluster rendering was out of scope.
- **Verified.**
  - Node `vm` harness on real `results.build` output for olive-trees, colorful-picture-frames
    and dishwasher-rack, 88/88:
    - No panel or `byBodyTag` identifiers and no "click to" strings are left.
    - Hover tips have no image or hints over all 1,771 occurrences, and 419 keyword tips keep
      their stats foot.
    - Read more shows up exactly when the text says more (1,730 with, 41 without), and the
      expanded view keeps the sentence `.hit`.
    - The B1 order holds.
    - The links are right.
    - C1 keep and reset, plus the read-more collapse, work through the real click handler.
    - Zoom opens on the card's image and syncs it on close.
    - Esc, the veil, × and a page scroll each unpin.
  - Playwright on a fresh server (port 5056), 57/57 at 1280 × 800 and at 600 × 900:
    - Hover card is compact with no buttons, and a keyword click does nothing.
    - The pinned card sits beside the click point inside the viewport, at the pinned width, and
      doesn't follow the mouse.
    - Image arrows work, with focus held.
    - Same-ASIN keep and different-ASIN reset, plus a disabled → at the last source.
    - A long dishwasher-rack review expands to 100vh − 16 and scrolls inside; the wheel never
      moves the page, at the top, the middle or the bottom.
    - Zoom opens on the same image; the wheel there doesn't close the card; Esc closes the zoom
      view, then the card.
    - A click on another chip through the veil, or on bare veil, closes without pinning.
    - A wheel on the veil and a scripted scroll both close the card.
    - `/` is ignored while pinned and works after.
    - Clusters, themes, keywords, the source toggle and search still render, with no page errors.
  - Screenshots are in `scratchpad/shots2/`.

## Settled: round 3 touch-ups (built 2026-09-25)

- **Zoom view is a box, not full screen.** `#zoom` is now a dimmed backdrop (ink at 50%) holding
  `.zbox`, `min(92vw, 1500px)` × `min(88vh, 1000px)`; at ≤ 640 px it is the viewport less 8 px a
  side. A click on the backdrop closes the zoom view only; the pinned card stays.
- **Stray focus ring on a chip after closing, fixed.** The pinned card handed focus back to its
  chip on close; after an Esc close the browser counted the page as keyboard-driven and drew the
  cobalt `:focus-visible` ring on the chip, which stayed. Now focus moves into the card and back
  to the chip only for a card opened from the keyboard (`pinState.kb`); a mouse-opened card
  leaves focus alone and blurs the chip or card control on close.
- Verified with Playwright at 1280 and 600 px (`scratchpad/r2/r3.py`, 13/13 each: no ring after
  Esc, veil, × or zoom-then-Esc; keyboard pin still focuses × and returns to the chip; box size;
  backdrop click; wheel zoom), and round 2's suite still passes 57/57 at both widths.
