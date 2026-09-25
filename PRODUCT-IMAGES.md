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
