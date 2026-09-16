# Phase 5 — Results UI with sentence attribution

## Context

Phases 0–4 produce, per parse, `tagged.json` (exact text per body plus the tags the model
emitted) and `clusters.json` (occurrences → unique tags → clusters, for features,
complaints and assembly/care). Nothing renders them yet: the run view only shows count
strips. Phase 5 is the payoff screen: ranked clusters per section, drill-down to every tag,
and for every tag the exact sentence it came from, highlighted, with the source listing's
image and full text one click away.

Attribution was the risk. It was prototyped against every parsed run on disk
(2,512 tag mentions, 7 groups) before this plan was written:

| Slice | Confident match |
|---|---|
| All tags | 92.0% |
| Listings | 99.9% |
| Reviews | 89.0% |
| English text only | 94.1% |
| Complaints | 85.4% |

Hand-checked precision of confident matches ≈ 97%. Of the unmatched 8%, 28% are non-English
reviews (the tagger translates; no lexical method can reach them) and the rest are true
paraphrases. A wrong sentence is worse than none, so those fall back to the full body.

### Decisions settled in scoping (all questions answered 2026-09-16)

| Area | Decision |
|---|---|
| Ranking | By mentions (occurrence count, what `clusters.json` already sorts by). Card shows both: `25 mentions · 15 distinct tags`. |
| Coverage | Card also shows `in 5 of 6 products` (`unique_asins` / products in run). |
| Chips | One chip per distinct tag **per source type**, count badge `×4`. A tag found in both listings and reviews appears in both columns with its own count. |
| Collapsed card | Title, one-sentence description, counts line, top 3 tags by mentions + `+N more`. |
| No confident match | Tooltip: first ~200 chars + "no exact sentence found". Panel: full body, no marks. |
| Highlight | Matched words only; adjacent matches merge into one mark. |
| Placement | Dedicated view `#/g/<slug>/r/<run>/p/<parse>`. Parse pills on the run view become links; selector when >1 parse. |
| Scope | Search terms / usage keywords deferred. Only the three clustered sections. |
| Where attribution runs | Server-side Python, computed at request time (ms), nothing written to disk. CLI prints the accuracy report. |

Deviation from IMPLEMENTATION_PLAN.md (flagged, not silent): sentence highlighting was
"Phase 6, deferred" there; it is the centrepiece here per the new brief. The plan's
secondary search-terms section is deferred instead. Plan's `.exp/.expin` table
expanders are replaced by card-level expand, since clusters are cards not table rows.

---

## 1. `pipeline/attribution.py` (new)

Deterministic, no network, no new dependencies. Port of the validated prototype at
`C:\Users\13477\AppData\Local\Temp\claude\...\scratchpad\attr_proto.py` (v3), with the
two fixes noted below.

**Pieces, in order:**

- `stem(word)` — Porter stemmer (~70 lines, already written in the prototype).
- `STOP` — standard English stopwords + contraction stubs (`dont`, `ive`) + purchase
  filler (`bought`, `ordered`, `arrived`) + `little`, `lot`, `bit`. `made`/`make` are
  **not** stopwords (`well made`).
- `SCAFFOLD` — words the tagger injects that rarely appear in the source: completion
  nouns (`build construction finish material design quality look feel appearance
  surface option feature style`) and normalised approval / tag-speak (`nice good great
  pretty beautiful perfect lovely excellent suitable required needed multiple various
  overall provides included`). Weight 0.25.
- `SYN` — stem-level fold applied after stemming: `leav→leaf shelv→shelf big/bigger→larg
  tini/smaller→small pictur/pic/imag→photo lightweight→light faux/artifici→fake
  colour→color`.
- `tokens(text)` — regex `[A-Za-z]+|\d+(?:\.\d+)?` (so `16oz`→`16`,`oz`; hyphens split).
  **Fix 1:** run the regex on the original text and strip apostrophes from the *token
  string* only (`don't`→`dont`), never from the text, so char offsets stay exact. The
  prototype stripped apostrophes first and shifted highlights by one char.
- `units(text)` → `[(start,end)]` — split on `\n`; then a bullet header before `: ` when
  ≤8 words; then `[.!?;]` followed by whitespace **or directly by a capital** (reviews
  often have `out.The`); also ` | `, ` • `, spaced dashes. Strip whitespace, drop empties.
- `idf` — per parse, over sentence units: `log((N+1)/(df+1))+1`; `idf_max = log(N+1)+1`.
- `match(t, s)` — equal; or one stem is a prefix of the other with prefix ≥4 and
  remainder ≤3 (`wood/wooden`, `real/realist`, `stack/stackabl`); or same except a final
  vowel with length ≥5 (`shine/shini`). No general "common prefix" rule (that made
  `resin` match `resist`).
- `attribute(tag, body_text, units, idf)`:
  - tag stems (deduped) with weights `idf × (0.25 if scaffold)`.
  - per unit: matched stems + char spans; `score = matched weight / total weight`.
  - best unit by `(score, matched count, prefer >2-token units, fewer tokens, earlier)`.
  - `distinct = max idf of matched non-scaffold stems / idf_max`.
  - **Fix 2:** `confident = score ≥ 0.999 or (score ≥ 0.4 and distinct ≥ 0.4)`. The
    prototype's distinctness rule wrongly rejected exact matches like "beautiful finish".
  - merge spans separated by no alphanumerics; return
    `{unit:[start,end], spans:[[s,e],…], score, confident}` or `{confident:false}`.
- `attribute_parse(tagged, clusters)` — builds idf once, walks every occurrence in every
  section, returns `{occ_id: result}`. Also returns `bodies` = `{body_id: {asin, type,
  text, units}}`.
- `__main__` — `python -m pipeline.attribution <slug> <run_id> [parse_id]` prints the
  table above (overall / per list / per body type / non-English share) plus N random
  matches with `[[marks]]`. This is the regression harness; run it before and after any
  tweak to `STOP`/`SCAFFOLD`/thresholds.

Constants (`THRESH=0.4`, `DISTINCT=0.4`, `SCAFFOLD_W=0.25`) live at the top of the module
with a comment pointing at the measured table; not in `config.py` (they are not user
tunables).

## 2. API — `app.py`

One new endpoint, next to `api_parse_result`:

```
GET /api/runs/<slug>/<run_id>/parses/<parse_id>/results
```

Returns a render-ready model so the browser does no joining:

```
{ product, parse_id, run_id, slug, generated_at, model, reasoning,
  products_total,
  sections: [{ key, title, status, error,
     clusters: [{ cluster_id, number, title, description,
                  total_tags, distinct_tags, unique_listings, unique_reviews, unique_asins,
                  listing_tags: [{ display, count, occ_ids }],   # sorted count desc, display asc
                  review_tags:  [{ display, count, occ_ids }],
                  preview: [{ display, count, occ_ids }] }]       # top 3 by count across both
  }],
  occurrences: { occ_id: { body_id, asin, body_type, text, match } },   # match from §1
  bodies: { body_id: { asin, type, text, review: {title, rating, date, verified_purchase,
                        source} | null, tags: {features:[…], complaints:[…], assembly_maintenance:[…]} } },
  products: { asin: { title, brand, main_image, link, price, rating, ratings_total } } }
```

- `tagged`/`clusters` read via `storage.read_json`; products via `storage.load_run`
  (edits applied, `include_deleted=True` so a removed ASIN still resolves its title/image).
- `bodies[].text` comes from `tagged.json`, never from the run: it is the text that was
  tagged, even if the review was edited afterwards.
- Review meta: `body_id.split(":", 2)` → `(asin, "review", review_id)` → find in
  `product.reviews`; `None` if deleted since.
- `preview` and the two tag lists are built from `unique_tags[].occ_ids` split by
  `occurrences[].body_type`. `distinct_tags = len(cluster.uids)`.
- Missing `clusters.json` → `400 "This parse hasn't been grouped yet — press Group tags on the run."`
  (the UI turns it into the empty state, not an error banner).
- Errors follow the existing pattern: `storage.StorageError` with a plain sentence.

Also: `api_run` already returns `parses`; no change. `renderParses` in the UI gains links.

## 3. UI — `static/index.html`

Vanilla JS, same file, same design system. New route + view + two overlays.

**Route** `#/g/<slug>/r/<run>/p/<parse>` → `renderResults(slug, runId, parseId)`.
Add to `route()` before the run match; `showView("results")`.

**Run view changes (`renderParses`)**: each parse pill becomes `<a href="#/g/…/p/…">`;
the newest one also gets a "View results" `.btn` in the action bar when `clusterLog.lists`
exists.

**Results view layout**
- Masthead: eyebrow `"<group> · results"`, h1 = cluster document's `product`; back link to
  the run.
- `.drop` bar: `data/<slug>/<run>/<parse>` in `.df`; a `<select>` of parses when >1
  (uses `.ctl select` styling copied from example-ui); model · reasoning `.hint`; a
  "Group tags" ghost button linking back to the run.
- `.sumstrip`: one `stat()` per section (clusters · mentions), plus `Attributed`
  (`% of mentions with a confident sentence`, tone good ≥90 / warn otherwise) — the honesty
  number.
- Three `<section>`s in `grouping.LISTS` order, each `.shead` with the section title and
  `.note` `"N clusters · M mentions"`. Failed section → `.note-line` with its error.
  Empty section → `.blank`.
- Cluster grid: `.cgrid` = `repeat(auto-fill, minmax(340px,1fr))`, gap 16. Each cluster a
  `.card.cl`:
  - header: `#<number>` mono micro-label, `h3` title (display font, 16px), description
    (muted, 12.5px).
  - counts line, mono 10.5px: `25 mentions · 15 distinct tags · 3 listings · 20 reviews ·
    in 5 of 6 products`.
  - collapsed: `.bubs` with 3 preview chips + a `.lnk` `"+12 more"`.
  - expanded: two blocks with `h4` labels **From listings (3)** / **From reviews (20)**,
    each a `.bubs` of chips; a block is omitted when empty. Toggle by clicking the card
    header (`.shead.clk` pattern, `.caret` ▸/▾). Expanded state per cluster kept in
    `localStorage` under `results:<slug>:<parse>` (UI pref only, matches the plan's rule).
- Chip: `.bub` restyled neutral per the plan — `--cobalt-wash` background, `--cobalt`
  text, count badge `<i>×4</i>` in mono. `data-occ` = first occ_id, `data-occs` = all.
  Chips whose first occurrence has no confident match get a dotted underline
  (`.bub.soft`) so the eye can tell "sentence found" from "full text only" before hovering.

**Hover → `#tip` rich card** (replaces the example's text-only tooltip; same fixed
positioning code, `pointer-events:none`, `white-space:normal`, `max-width:420px`):
- left: 64px thumb from `products[asin].main_image` (or "no image" box).
- right: `.tt` `"B0DFQB68PT · listing"` or `"· review ★★★★☆ · 2026-04-17"`; `.tr` product
  title clamped to 2 lines.
- body: the matched sentence with `<mark>` on each span; or the first ~200 chars of the
  body plus `.tt` `"no exact sentence found — click for full text"`.
- footer `.tt`: `"1 of 4 sources · click to open"` when `count > 1`.
- Rendered with `escapeWithMarks(text, spans)`: escape segments between spans and wrap
  spans in `<mark>`, never `innerHTML` on raw text.

**Click → slide-out panel** `#panel` (new component; `position:fixed; right:0; top:0;
height:100%; width:min(560px,100%); background:var(--glaze); border-left:1px solid
var(--rule); overflow:auto; z-index:85`, plus a `.veil`-style backdrop that closes it;
Escape closes; slides in with a 160ms transform, honoring `prefers-reduced-motion`):
- header: the tag text as `h3`, cluster title as eyebrow, close `×`.
- source pager when `occs.length > 1`: `"Source 2 of 4"` with ‹ › buttons; each page is one
  occurrence.
- product block: thumb 96px, title, `ASIN` linked to Amazon (`link || source_url`,
  `target=_blank rel=noopener`), price · rating · brand pills (reuse `.pill`).
- for reviews: `.rv .rh` row (stars, title, date · verified · pasted) reusing run-view CSS.
- full text: `white-space:pre-wrap`; the matched unit wrapped in `<span class="hit">`
  (`--amber-wash` background) and words in `<mark>`. No marks when not confident, with a
  `.note-line.info` saying the sentence couldn't be pinned down.
- "Other tags from this source": the body's features / complaints / care tags as plain
  `.bub` chips (from `bodies[id].tags`), hover shows their sentence too. Clicking one
  switches the panel to that tag (same body).

**State**: `resultsState = {slug, runId, parseId, data, expanded:Set}`; tooltip and
panel read from `data.occurrences` / `data.bodies` / `data.products` by id. No refetch
on hover or click.

**Reuse**: `api()`, `esc()`, `stat()`, `plural()`, `stars()`, `setHeader()`,
`confirmModal()`, `.pill`, `.rv`, `.note-line`, `.blank`, `.strip`, `.hint`, `.lnk`.
Tooltip positioning copied from `example-ui.html.html:1369-1378`.

## 4. Files

| File | Change |
|---|---|
| `pipeline/attribution.py` | new — matcher + `attribute_parse` + CLI report |
| `app.py` | new `GET …/parses/<parse_id>/results` |
| `static/index.html` | route, results view, tooltip card, slide-out panel, parse links |
| `IMPLEMENTATION_PLAN.md` | append "Settled in Phase 5" block: the measured table, the confidence rule, the scoping decisions above |

No changes to tagging, grouping, storage or data files. No new pip dependencies.

## 5. Verification

1. `python -m pipeline.attribution wooden-serving-bowls 2026-09-10_172613` → table shows
   ≈94% overall, listings ≈100%; the printed samples include `handcrafted design` →
   `[[handcrafted design]]`, `wood grain and warm tones` → `[[grain]] and [[warm tones]]`,
   `mango wood construction` → `[[Mango wooden]]`. Repeat for `mini-polaroid-frames` and
   `artificial-mums-flowers` (the three grouped runs).
2. Unit sanity in a REPL: `sum(c.total_tags)` per section equals `len(occurrences)`;
   every `occ_id` in `results.occurrences` has a match object; every span's text, sliced
   from `bodies[body_id].text`, is non-empty and contains no leading/trailing space.
3. `python app.py` → open a grouped run → click the parse pill → results view: three
   sections, clusters in the same order as `clusters.md`, count line matches the md's
   tag counts.
4. Hover `handcrafted design` in the bowls run → tooltip shows the B0DFQB68PT image and
   the exact sentence with one mark. Hover a `×N` chip → "1 of N sources". Hover a
   dotted chip → "no exact sentence found".
5. Click a review chip → panel shows stars/date/verified, the sentence band and marks,
   the Amazon link opens the right ASIN; pager walks all sources; Escape closes.
6. Open an ungrouped parse URL by hand (e.g. olive-trees) → "Group tags first" empty
   state, no red banner.
7. Narrow the window to 600px → cards stack, panel is full width, tooltip stays on
   screen.
