# PRD & Implementation Plan — Competitor Feature-Benefit Extractor

## Context

You sell home decor on Amazon (picture frames, mugs, artificial flowers). Understanding *which concrete features and benefits customers actually care about* currently means reading competitor bullets and reviews by hand — slow, and impossible to do systematically across a dozen ASINs.

This tool automates that: paste competitor URLs → pull listing + review text via Rainforest API → extract structured tags with an LLM using your existing `product-research-extractor` rules → cluster those tags into semantic themes → display themes as ranked boxes you can drill into, all the way back to the exact review that produced each tag.

**Outcome:** for any product category, a ranked list of the concrete benefits competitors claim and customers confirm, with evidence attached to every claim.

Everything is local, single-user, file-backed. No database, no auth, no build step.

---

## Decisions locked in scoping

| Area | Decision |
|---|---|
| Architecture | Flask backend + one self-contained `index.html`. `python app.py` starts server and opens browser. |
| Keys/config | `config.py`, edited by hand; falls back to env vars. |
| Storage | `data/<group-slug>/<run-id>/` — timestamped runs, full history, nothing overwritten. |
| Reviews | Always take `top_reviews` from `type=product`. *Attempt* `type=reviews` as optional enrichment, degrade silently. Probe script settles the real cap. |
| Review filter | Tag all reviews; drop only empty bodies. Negative reviews are signal. |
| Extra fields | specifications, material, color, model_number, categories_flat, bestsellers_rank_flat. |
| Editable | title, bullets, review titles/bodies, price, rating + add/delete reviews. Raw payload never mutated. |
| Tag schema | Full skill coverage plus `complaints` and `assembly_maintenance`: `search_terms`, `features`, `complaints`, `assembly_maintenance`, `usage_keywords{spaces, placements, occasions, used_for}`, `avoided`. |
| Tagging | One OpenAI call per body, `gpt-5.6-terra`, reasoning `low`, ~8 concurrent, strict JSON schema, 2 retries w/ backoff. |
| Grouping | `features` only. Normalize → chunk → LLM → merge, on `gpt-5.6`. |
| Scale target | 10–20 ASINs per group (~1,200–3,200 raw tags). |
| Box metrics | Total tags · unique listings · unique reviews. Sorted by total tags desc. |
| Provenance | Hover tooltip (ASIN + title + snippet) → click for full slide-out panel. |
| Progress | Background job + polling, incremental counts ("7/12 ASINs"). |
| Logging | `.sumstrip` on screen + `run_log.json` on disk, with per-item failure reasons. |

**Deferred by explicit agreement:** deterministic sentence highlighting in the source panel (Phase 6); a deeper grouping ruleset (Phase 6).

---

## Two findings that shape the build

1. **`type=product` returns ~8 reviews.** Verified against `data templates/rainforest_api_example_output.json` (`top_reviews` has 8 entries for an ASIN with 1,845 ratings). You believe `type=reviews` may be deprecated; Rainforest's docs site is JS-rendered and unfetchable, so this can only be settled empirically. Phase 1 ships `probe_reviews.py` for exactly this. The app is built so review depth is a config number, not an assumption.

2. **No BeautifulSoup anywhere.** `amazon_web_scrape.py` uses Rainforest's JSON API. Confirmed — no HTML parsing in the pipeline.

---

## Project layout

```
AI Product Research/
  app.py                    # Flask app: routes + static + browser launch
  config.py                 # API keys, model names, tunables  ← you edit this
  requirements.txt
  probe_reviews.py          # one-off: how many reviews can we actually get?
  pipeline/
    asins.py                # extract_amazon_asin  (lifted from amazon_web_scrape.py)
    rainforest.py           # product + review fetch, parallel
    normalize.py            # raw payload → extracted.json; tag text normalization
    tagging.py              # per-body OpenAI tagging
    grouping.py             # normalize → chunk → group → merge
    storage.py              # group/run folder IO, slugs, edits overlay
    jobs.py                 # background job runner + progress registry
    prompts/
      tag_body.md
      group_tags.md
      merge_groups.md
  static/
    index.html              # entire UI, vanilla JS, no build
  data/
    <group-slug>/
      group.json
      <run-id>/             # 2026-09-10_143022
        raw/<ASIN>.product.json
        raw/<ASIN>.reviews.p<N>.json
        extracted.json
        edits.json
        run_log.json
        parse-<ts>/
          tagged.json
          groups.json
          parse_log.json
```

### Code to reuse verbatim from [amazon_web_scrape.py](amazon_web_scrape.py)

- `extract_amazon_asin` ([lines 13–73](amazon_web_scrape.py#L13-L73)) — 6-pattern ASIN regex, already handles `/dp/`, `/gp/product/`, `?asin=`. Move to `pipeline/asins.py` unchanged.
- `get_amazon_product_data` ([lines 75–105](amazon_web_scrape.py#L75-L105)) — including its 401/403/404 status handling.
- `get_amazon_product_data_parallel` ([lines 117–151](amazon_web_scrape.py#L117-L151)) — `ThreadPoolExecutor`, `max_workers=min(len, 5)`, per-ASIN failure isolation. Extend to report failures rather than only print them.
- `get_feature_bullets_from_json` / `get_reviews_from_json` ([lines 154–235](amazon_web_scrape.py#L154-L235)) — field paths and defensive `isinstance` guards. Drop the pandas dependency; return plain dicts.
- Emoji-strip regex from `concatenate_reviews` ([lines 267–277](amazon_web_scrape.py#L267-L277)) — apply to review text before tagging.

---

## Data model

### `extracted.json` (machine output, never edited)

```json
{
  "group": "Vintage Gold Picture Frames",
  "run_id": "2026-09-10_143022",
  "amazon_domain": "amazon.com",
  "fetched_at": "2026-09-10T14:30:22Z",
  "products": [{
    "asin": "B08P5LPZFJ",
    "source_url": "<the URL you pasted>",
    "link": "<canonical from payload>",
    "title": "...", "brand": "...",
    "price": {"raw": "$19.99", "value": 19.99, "currency": "USD"},
    "rating": 4.7, "ratings_total": 1845,
    "rating_breakdown": {...},
    "main_image": "https://...", "images": ["https://...", "..."],
    "categories_flat": "...", "bestsellers_rank_flat": "...",
    "material": "...", "color": "...", "model_number": "...",
    "specifications": [{"name": "...", "value": "..."}],
    "feature_bullets": ["...", "..."],
    "reviews": [{
      "id": "R2FVAC3X5DI0TO", "title": "...", "body": "...",
      "rating": 5, "date": "2026-04-17",
      "verified_purchase": true, "helpful_votes": 0,
      "source": "product"
    }],
    "warnings": ["Rainforest returned no reviews for this ASIN"]
  }]
}
```

Price comes from `product.buybox_winner.price` (confirmed in the sample payload); images from `product.images[].link`; `main_image` from `product.main_image.link`.

### `edits.json` (your overrides, applied as an overlay)

```json
{ "B08P5LPZFJ": { "title": "...", "feature_bullets": ["..."],
                  "reviews": [{"id": "manual_1", "title": "...", "body": "..."}] } }
```

`storage.load_run()` returns `extracted.json` deep-merged with `edits.json`. The raw payload and `extracted.json` stay pristine, so you can always see what Rainforest actually returned.

### Body construction (deterministic, at parse time)

| Type | `body_id` | Text |
|---|---|---|
| listing | `{ASIN}:listing` | `title \n bullet_1 \n … \n bullet_n` |
| review | `{ASIN}:review:{review_id}` | `review_title \n review_body` |

### `tagged.json`

```json
{ "bodies": [{
    "body_id": "B08P5LPZFJ:listing", "asin": "B08P5LPZFJ", "type": "listing",
    "text": "<the exact text sent>",
    "tags": {
      "search_terms": ["5x7 picture frames"],
      "features": ["shatter-resistant plastic cover"],
      "complaints": [],
      "assembly_maintenance": ["wipe with a dry soft cloth only"],
      "usage_keywords": {"spaces": ["office"], "placements": ["mantel"],
                         "occasions": ["wedding"], "used_for": ["family photos"]},
      "avoided": [{"phrase": "adds sophistication", "reason": "vague"}]
    },
    "status": "ok", "error": null,
    "usage": {"input_tokens": 812, "output_tokens": 240}
  }],
  "skipped": [{"body_id": "...", "text": "Love it!!", "reason": "under 30 characters"}],
  "failures": [{"body_id": "...", "reason": "Rate limited after 3 attempts"}] }
```

`avoided[].reason` is one of `vague` · `price_claim` · `listing_fidelity` ·
`sku_spec` · `no_product_attribute` · `generic_filler` · `service_not_product` ·
`unsupported`. An all-empty tag set is `status: "ok"`, not a failure.

### `groups.json` — three layers, so provenance survives normalization

```json
{
  "generated_at": "...", "model": "gpt-5.6", "tag_type": "features",

  "occurrences": [
    {"occ_id": "o_0007", "text": "sturdy anti-tip base", "norm": "sturdy anti tip base",
     "body_id": "B08P5LPZFJ:review:R2FV...", "asin": "B08P5LPZFJ", "body_type": "review"}
  ],

  "unique_tags": [
    {"uid": "u_012", "display": "sturdy anti-tip base",
     "norm": "sturdy anti tip base", "occ_ids": ["o_0007", "o_0051"]}
  ],

  "groups": [
    {"group_id": "g_01", "title": "Sturdy Anti-tip Base", "uids": ["u_012", "u_087"],
     "total_tags": 10, "unique_listings": 5, "unique_reviews": 3, "unique_asins": 6}
  ]
}
```

**This is the key structural decision.** Normalization and grouping both operate on layers *above* occurrences. Every occurrence keeps its exact original wording, its body, and its ASIN — so hover-to-source works no matter how much collapsing happened upstream, and Phase 6's sentence highlighting has the raw string to search for.

---

## Phases

Each phase ends with something you can run and look at.

### Phase 0 — Scaffold + Groups view

- `config.py` with `RAINFOREST_API_KEY`, `OPENAI_API_KEY`, `OPENAI_TAG_MODEL="gpt-5.6-terra"`, `OPENAI_GROUP_MODEL="gpt-5.6"`, `AMAZON_DOMAIN="amazon.com"`, `REVIEW_TARGET=30`, `RAINFOREST_WORKERS=5`, `OPENAI_WORKERS=8`, plus `PRICE_PER_1M_INPUT/OUTPUT` per model for cost estimates (**verify these rates against OpenAI's pricing page at build time — I could not confirm `gpt-5.6-terra` pricing from docs**).
- `app.py`: Flask, serves `static/index.html`, opens browser via `webbrowser.open`, binds `127.0.0.1`.
- `pipeline/storage.py`: slugify, create/list groups, create runs, atomic JSON write, edits overlay.
- `static/index.html`: the full design system (below) + Groups view — list existing groups, create a new one with a name + a textarea of URLs/ASINs.

**Verify:** `python app.py` → create group "Test Frames" → it appears on reload, `data/test-frames/group.json` exists.

### Phase 1 — Step 1: extraction

- `pipeline/asins.py`, `pipeline/rainforest.py`, `pipeline/normalize.py`.
- `probe_reviews.py`: walks `type=reviews` pages for one ASIN until responses repeat or error; prints unique review count, page cap, credits spent, and whether the endpoint works at all. **Run this before finalizing `REVIEW_TARGET`.**
- Review strategy: product call always → if `type=reviews` works, page until `REVIEW_TARGET` or the API stops returning new IDs → dedupe by review `id` → record actual count. If it fails, log the reason plainly ("Rainforest reviews endpoint unavailable — using the 8 reviews from the product call") and carry on.
- `pipeline/jobs.py`: `run_job(fn)` spawns a thread, stores `{state, done, total, label, errors[]}` in a module-level dict. `POST /api/groups/<slug>/extract` returns `202 {job_id}`; `GET /api/jobs/<job_id>` is polled every ~700ms.
- Extract view: summary strip + ASIN cards (image, title, price, rating, bullet count, review count, warning pills), read-only for now.

**Verify:** paste 3 real URLs → progress bar counts up → cards render → `data/<slug>/<run>/raw/*.json` written → summary reports ASINs succeeded, % with bullets, % with reviews, credits remaining.

### Phase 2 — Editing & approval

- Double-click to edit title, bullets, price, rating, review title/body. Add/delete reviews (for pasting manually when Rainforest returns none). Escape cancels, blur/Enter saves.
- `PUT /api/runs/<slug>/<run_id>/edits` persists to `edits.json`; UI shows an "edited" marker on changed fields.
- Warnings (0 bullets / 0 reviews) shown as amber pills. **Never block** the Parse button.

**Verify:** edit a bullet, reload → edit persists; `extracted.json` diff shows it untouched.

### Phase 3 — Step 2: tagging

- `pipeline/prompts/tag_body.md` — the `product-research-extractor` rules transcribed with identical logic:
  - **Search terms:** 2–5 word noun phrases a shopper would type; must contain the product noun or its category; SKU specifics allowed; pull from titles, bullet headers, and review wording alike.
  - **Features:** concrete, factual, distinct, each complete on its own and covering one component or one theme. Exclude vague praise, price/value claims, listing-fidelity claims, non-generalizing SKU specs (color/shape/size/pack count), and reviewer framing that yields no product attribute. Paraphrase verbose or indirect wording into the plain benefit; keep clean wording as-is. Name the component when the attribute needs it — leave bare if the source never says what it modifies. Group causally-linked or same-theme attributes into one label; split independent benefits.
  - **Usage keywords:** 1–2 words each, under spaces / placements / occasions / used_for. Skip generic filler (home, space, room, area, decor). Modifiers must be factually concrete.
  - **Faithfulness:** every label traces to a specific line — if you can't point to the line, drop it. Paraphrase to clarify, never to add. Deduplicate. Carry both sides of a factual contradiction and flag it.
  - **Avoided:** every rejected phrase with a one-line reason.
  - Include the skill's worked picture-frame example verbatim as a few-shot.
- `pipeline/tagging.py`: `client.responses.create(model=..., input=..., text={"format": {"type":"json_schema","name":"tagged_body","strict":True,"schema":{...}}})` — confirmed current shape via context7. `ThreadPoolExecutor(OPENAI_WORKERS)`, 2 retries with exponential backoff on rate-limit/timeout/refusal, then record failure and continue.
- Pre-flight estimate: body count × mean chars ÷ 4 × input rate, shown in the Parse confirm dialog. Actuals from `response.usage` written to `parse_log.json`.

**Verify:** parse a 3-ASIN run → `tagged.json` validates against the schema → hand-check one listing's output against the skill's rules → progress bar counts bodies → cost estimate lands within ~20% of actual.

**Settled in Phase 3 — the sixteen judgment calls the transcription needed.** The
skill was written for a human pasting a whole listing; a per-body prompt has to
answer things the skill never faced. Decided, and now live in
`pipeline/prompts/tag_body.md`:

1. **Complaints get their own array.** Negative reviews are signal, but the skill
   only knows how to extract benefits. Putting `glass arrives shattered` into
   `features` would have Phase 4 clustering it next to `shatter-resistant cover`.
   Scope is product + packaging; seller conduct, price framing, and vague
   negatives go to `avoided`. Phase 4 still groups `features` only.
2. **Bare attributes get completed only when they read as incomplete alone** —
   `"Sturdy"` → *sturdy build*, while `scratch-resistant` and `easy to hang`
   stay as written. A deliberate override of the skill's "leave it bare" line.
   Never guess a *specific* component the source didn't name.
3. **Reviews carry the product title as context, never as a tag source.** Without
   it the model can't resolve "it"; with it unrestricted, every review would echo
   the listing and inflate Phase 5's frequency counts.
4. **No evidence quotes and no contradiction flagging** — both considered,
   both dropped. Phase 6 still reconstructs provenance by fuzzy matching.
5. **No review rating in context.** Text alone decides polarity.
6. **Bodies under 30 chars are skipped**, recorded as `skipped` and never as a
   failure. The threshold is blunt — `"Easy to hang"` is a real feature and only
   12 characters — so every skipped body's full text goes to `parse_log.json`.
   Lower `MIN_TAG_CHARS` and re-parse if a genuine feature turns up there.
7. **No caps on tag counts** except `avoided`, which is 6 with an enum reason so
   rejections are countable across a run.
8. **Lowercase, singular, no trailing punctuation**; proper nouns and internal
   hyphens kept.

**Measured on the first real parse** (48 bodies, 5 ASINs, 2026-09-11): 48/48
tagged, 0 failures, 26s, $0.066 against a $0.079 estimate (17% over). 9 bodies
correctly yielded nothing — pure praise, listing-fidelity, or the shopper's own
situation. Re-tagging 15 bodies at `medium` reasoning gave identical feature sets
on 9 of 15 and the same total feature count (30 vs 31), the differences being
word order and merge boundaries rather than better extraction — so `low` stays.

### Phase 4 — Step 3: grouping

- **Pass 1 (deterministic, free):** lowercase → strip punctuation except internal hyphens → collapse whitespace → drop leading articles → naive plural fold (trailing `s` where `len>3` and not `ss`/`us`/`is`). Identical `norm` → one `unique_tag`; `display` = the most frequent original form. Typically collapses 30–40%.
- **Pass 2 (LLM):** unique tags sorted by `norm` (so near-neighbours land in the same chunk), chunked at ~150, each chunk one `gpt-5.6` call returning `[{title, indices[]}]`. Rules in `group_tags.md`:
  - One product attribute or benefit theme per group. Same attribute of the same component groups together; the same adjective on different components does not (`sturdy base` ≠ `sturdy frame`).
  - Every tag index appears in exactly one group.
  - Singletons are allowed. Never force-merge to tidy the output.
  - Title: 2–4 words, concrete noun phrase, drawn from the language of the tags themselves. Never name an attribute no tag states.
- **Pass 3 (LLM merge):** all chunk titles + up to 5 sample tags each → one call → merge map for groups that are the same theme.
- **Validation:** after every call, assert each index appears exactly once. Orphans become singletons and are logged, never dropped.
- Counts computed from occurrences: `total_tags` = occurrence count; `unique_listings` / `unique_reviews` = distinct `body_id` by type; `unique_asins` = distinct ASINs.

**Verify:** `sum(total_tags) == len(occurrences)`; every `occ_id` reachable from exactly one group; eyeball 5 groups for coherence.

### Phase 5 — Step 4: results UI

- Group boxes as `.card`, sorted by `total_tags` desc, in a responsive grid. Header: title, then `10 tags · 5 listings · 3 reviews` in mono micro-label style.
- Three expandable subheadings per box — **All tags**, **From listings**, **From reviews** — rendering tag chips.
- Hover a chip → `#tip` tooltip with ASIN, product title, body type, ~200-char snippet.
- Click a chip → right slide-out panel: full body text, review rating/date/verified, ASIN metadata, link to the Amazon listing. When a tag has multiple occurrences, the panel lists them and you page through.
- Secondary section below: search terms and usage keywords as flat frequency lists (spaces / placements / occasions / used_for), each clickable to the same panel.
- **Re-parse** button behind a confirm dialog showing the cost estimate; writes a new `parse-<ts>/` folder so you can compare prompt versions. A parse-run selector appears once more than one exists.

**Verify:** open a parsed run → boxes sorted correctly → expand each subheading → hover shows the right ASIN → click shows the full review → counts on the box match the expanded lists.

**Settled in Phase 5 (built 2026-09-16).** Sentence highlighting moved up from Phase 6
to the centre of this phase, and the secondary search-terms / usage-keywords section
moved out to a later one. What was decided and measured:

- **Attribution is deterministic and lives in `pipeline/attribution.py`.** Each body is
  split into sentence-like units (newlines, `.!?;`, bullet headers before a colon, and
  the `out.The` no-space breaks reviews are full of). Words are Porter-stemmed with a
  short irregular map (leaf/leaves, big/large, picture/photo). Every tag word is weighted
  by rarity across the parse (log IDF over units), with the scaffolding the tagger
  injects — completion nouns like *build*, *finish*, and normalised approval like *nice*,
  *suitable* — at a quarter weight. The best unit wins when ≥40% of the tag's weight is
  present **and** at least one matched word is distinctive (top 60% of rarity), or when
  every tag word is present. Highlights are the matched words, merged when adjacent.
- **Measured over 2,512 mentions across all seven parsed groups:** 92.0% confident
  overall · listings 99.9% · reviews 89.0% · English text only 94.1% · complaints 85.4%,
  with ≈97% of confident matches hand-verified as the right sentence. Of the 8% left,
  28% are non-English reviews (the tagger translates; nothing lexical can reach them),
  the rest are true paraphrases. Both show the whole body instead — a wrong sentence is
  worse than none. `python -m pipeline.attribution <slug> <run>` prints this table for
  one parse; run it before and after touching the word lists or thresholds.
- **Results are a render-ready model** from `GET …/parses/<parse>/results`
  (`pipeline/results.py`): clusters with tags split into listing and review chips,
  every mention attributed, and the bodies and products they point at, keyed by id.
  Computed on request (≈100 ms), nothing written to disk.
- **UI decisions from scoping.** Clusters rank by mentions (occurrences), and the card
  shows both mentions and distinct tags plus listings, reviews and "in N of M products".
  One chip per distinct tag per source, with a `×N` count; hover shows the first source,
  click pages through all. Collapsed cards preview the top three tags. A dotted chip
  means no confident sentence: hover shows the first 200 characters, the panel the full
  text unmarked. Results are their own view (`#/g/<slug>/r/<run>/p/<parse>`).
- **Tagged-but-ungrouped parses are never rendered.** Only a grouped parse is a link on
  the run and group views; the results URL for one that isn't shows an empty state with
  a link back to the run, where Group tags already lives.

### Phase 6 — Deferred refinements (flagged, not built yet)

- **Search terms and usage keywords.** Flat frequency lists (spaces / placements /
  occasions / used_for), each chip opening the same source panel. The data is in
  `tagged.json` already; only the section is missing.
- **Deeper grouping rules.** The four rules above are a starting point. Once you've seen real output, we tighten them — likely around component-vs-attribute boundaries and how aggressively near-synonyms merge.

---

## Design system — matching [example-ui.html.html](example-ui.html.html)

Copy the `:root` block ([lines 11–21](example-ui.html.html#L11-L21)) verbatim: porcelain `#F4F5F1` ground, glaze `#FFFFFF` surfaces, ink `#131A2A`, cobalt `#1B3A8C` accent, celadon/iron/amber for good/bad/warn, 1px `--rule` borders, 3px radii. Fonts: Bricolage Grotesque (display), IBM Plex Sans (body), IBM Plex Mono (labels/numbers).

Reuse these components rather than inventing new ones:

| Need | Existing class |
|---|---|
| Page shell | `.wrap` (max-width 1240px) |
| Title block | `.masthead` + `.eyebrow` + `h1` |
| Run stats | `.sumstrip` / `.sm` (`.t` label, `.v` value, `.s` sub) |
| Action bar | `.drop` + `.btn` / `.btn.ghost` |
| Section header | `.shead` + `.note` + `.lede` |
| Group boxes, ASIN cards | `.card` |
| Warning/status badges | `.pill.ok` / `.pill.warn` / `.pill.bad` / `.pill.na` |
| View switcher | `.chips` / `.chip[aria-pressed]` |
| Tag chips | `.bub`, restyled neutral (`--cobalt-wash` bg, `--cobalt` text) |
| Expandable rows | `.exp` / `.expin` + `.caret` |
| Hover tooltip | `#tip` + `.tt` / `.tr` |
| Confirm dialogs | `.veil` + `.modal` + `.fld` / `.mbar` |
| Empty state | `.blank` |
| Errors | `.err` (iron on iron-wash) |

Same conventions: vanilla JS, no framework, no build step, `localStorage` for UI prefs only (last group, expanded state). One new component needed — a progress bar; build it from `--cobalt` on `--rule-soft` inside the existing `.drop` bar.

---

## Error handling

Every failure surfaces in plain language, never a stack trace:

| Condition | Message |
|---|---|
| Bad/missing Rainforest key | "Rainforest rejected the API key. Check `RAINFOREST_API_KEY` in config.py." |
| ASIN not found | "B0XXXXXXXX wasn't found on amazon.com — check the URL." |
| Credits exhausted | "Rainforest credits are used up. 12 of 20 ASINs were fetched." |
| `type=reviews` unavailable | "Rainforest's reviews endpoint didn't respond — using the 8 reviews from the product listing." |
| URL yields no ASIN | Listed before the run starts, so you can fix it. |
| OpenAI rate limit | Retried twice, then: "OpenAI was rate limited — 8 of 390 bodies weren't tagged." Failed bodies listed by ID. |
| Grouping index mismatch | Silently repaired into singletons, reported in `parse_log.json` and the summary. |

Partial success is always preferred over total failure, and always reported.

---

## End-to-end verification

1. `pip install -r requirements.txt` (`flask`, `requests`, `openai`).
2. Put both keys in `config.py`.
3. `python probe_reviews.py B08HVLJBF6` → read the actual review ceiling → set `REVIEW_TARGET`.
4. `python app.py` → browser opens at `127.0.0.1:5000`.
5. Create group "Vintage Gold Picture Frames", paste 3–5 real competitor URLs, click **Extract**.
   - Progress counts up; cards render with images, prices, ratings, bullets, reviews.
   - `data/vintage-gold-picture-frames/<run>/raw/` holds one JSON per ASIN.
   - Summary reports ASINs succeeded/failed, % with bullets, % with reviews, credits remaining.
6. Double-click a bullet, edit it, reload → change persists in `edits.json`, `extracted.json` unchanged.
7. Click **Parse results**, confirm the cost estimate.
   - Progress counts bodies; `tagged.json` then `groups.json` appear.
   - Spot-check one listing's tags against `product-research-extractor`'s rules by hand: no vague praise, no price claims, components named where required.
8. On the results view: boxes sorted by total tags; expand **From reviews**; hover a chip → correct ASIN in tooltip; click → full review in the panel.
9. Reload the app, open the group from the home view → the parsed run loads from disk unchanged.
10. Sanity assertion (run once in a REPL): `sum(g["total_tags"] for g in groups) == len(occurrences)`.

---

## Settled in Phase 1 — measured, not assumed

Three things about Rainforest that only showed up by asking it (all measured 2026-09-10):

1. **`type=reviews` is dead.** HTTP 503, *"reviews request type is temporarily unavailable. You have not been charged for this request."* The app checks once per run — not once per ASIN — and falls back with a plain-language note. It costs nothing while the endpoint is down, and picks the capability back up automatically if Rainforest restores it.

2. **`top_reviews` is flaky, not stable.** The same ASIN, same request, returns reviews about **62% of the time** (measured over 8 consecutive calls: 5 hits, 3 misses, alternating). This was the real cause of patchy review coverage — not a per-ASIN property. Every hit returns the *identical* review set, so there is nothing to gain past the first one. `PRODUCT_ATTEMPTS = 4` retries only when it can help (the listing has ratings but this response had none), giving ~98% coverage at ~1.6 credits per ASIN.

3. **A dead ASIN does not 404.** Rainforest answers HTTP 200 with `success: true` and an empty `product` object. Detected via a missing title and reported as a failed fetch, so a phantom competitor can't quietly inflate the success count.

**Review depth verdict:** 8–13 reviews per ASIN, which is what the product call carries. `REVIEW_TARGET` stays as an upper bound that self-limits. This makes Phase 2's manual review-paste more valuable than originally expected — for the handful of listings Rainforest never yields reviews for, pasting is the only route. Re-run `probe_reviews.py` occasionally to see whether the reviews endpoint has come back.
