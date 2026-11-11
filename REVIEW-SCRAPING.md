# Reviews from Amazon directly

Rainforest stopped returning reviews in November 2026 (decorative-books, 2026-11-10: 45 calls
for 12 listings, 33 of them retries, 0 reviews). Reviews now come from Amazon's own review
pages, read with a saved login; Rainforest supplies the listing only.

## Decisions (scoped 2026-11-11)

| # | Decision |
|---|---|
| 1 | **Amazon site** is a setting: Canada (`amazon.ca`, default) or USA (`amazon.com`). It drives both the Rainforest listing call and the review fetch for new extractions. |
| 2 | **Up to 50 reviews per listing**, adjustable in Settings (20 / 50 / 100). |
| 3 | **Balanced by star rating**: ten at a time from 1★ up to 5★, round and round, until the cap is reached or every rating runs out. Within a rating, Amazon's "Top reviews" order. |
| 4 | **Rainforest**: one call per ASIN; another (3 at most) only when the call fails or returns no listing. Never for missing reviews. The `type=reviews` probe is gone. |
| 5 | **Pace**: reviews run alongside the listing fetch, one ASIN at a time on one Amazon session, about one request a second. |
| 6 | **Trigger**: Extract fetches both. A run with listings that have no reviews shows **Fetch reviews** (no Rainforest credits). Parse results never fetches. |
| 7 | **Sign-in lapsed or robot check**: the run finishes with listings only and a plain note; ASINs not reached are left for Fetch reviews. **Sign in to Amazon** opens a browser window; the app never opens one unasked. |
| 8 | **Storage**: `data/<group>/<run>/raw/<ASIN>.amazon_reviews.json`. |

## How it is built

- `amazon_reviews_new.py` (repo root) is the scraper, imported untouched. It needs
  `beautifulsoup4`, `lxml` and, for the sign-in window, `playwright` + `playwright install chromium`
  — all in `.venv`. Logins live in `amazon_cookies.<site>.txt` (git-ignored).
- `pipeline/amazon_reviews.py` wraps it: `Session(domain)` (one signed-in client per run),
  `fetch_reviews(session, asin, cap)` (its own paging loop, one cursor per star rating, because the
  script's `scrape_filter` hides its cursor and hard-codes `sortBy=recent`), `to_reviews(payload)`
  (the app's review shape, `source: "amazon"`, Amazon's own `R…` ids), and `sign_in(domain)`.
  Failures are typed: `SignInNeeded` and `RobotCheck` end the walk, `ReviewError` fails one ASIN.
  It never calls the script's `scrape_all`, which opens a browser on its own.
- `pipeline/extract.py`: `run_extraction` starts a review thread beside `rainforest.fetch_all`;
  progress reads `Listings 7/12 · Reviews 4/12`. `fetch_missing_reviews` fills an existing run from
  the site that run was extracted from, writes into `extracted.json` (late machine output, not an
  edit; a listing whose reviews were edited by hand gets them in `edits.json`, where its list
  lives), and replaces run notes that have gone stale. `run_log.json` gains a `reviews` block
  (`site`, `fetched`, `failed`, `requests`, `stopped`).
- `pipeline/settings.py`: `amazon_site`, `reviews_per_asin`. `config.py`: `AMAZON_DOMAIN` default
  `amazon.ca`, `REVIEWS_PER_ASIN`, `AMAZON_REVIEW_SORT` (`helpful` / `recent`), `AMAZON_DELAY`,
  `PRODUCT_ATTEMPTS` 4 → 3. `REVIEW_TARGET` and `TRY_REVIEWS_ENDPOINT` are gone.
- Routes: `POST /api/runs/<slug>/<run>/fetch-reviews`, `GET /api/amazon/status`,
  `POST /api/amazon/login`. UI: the Settings dialog's Amazon section, and a Reviews bar on the run
  page with Fetch reviews and Sign in to Amazon.

## Settled (built 2026-11-11)

- **Star filters and Top-reviews order work on both sites.** B0CSYT6NLX, cap 60: amazon.ca 60
  reviews in 7 requests, amazon.com 60 in 6; every review carried the rating it was filtered for
  (0 mismatches of 120); `sortBy=helpful` returns a different set from `recent`; the AJAX second
  page works. On amazon.ca the listing had only 5 two-star and 6 three-star reviews, so the rest
  came from five-star: balance is best-effort, bounded by what exists.
- **New extraction, amazon.ca, 2 ASINs:** 2 Rainforest calls, 11 Amazon requests, 15.9 s, 103
  reviews. Rainforest on amazon.ca still sent some `top_reviews` (6 and 13); they are merged by id,
  which is why a listing can show a few more than the cap.
- **Fetch reviews on decorative-books (amazon.com):** 11 listings, 463 reviews, 80 requests,
  111 s, no Rainforest credits, `edits.json` byte-identical. Roughly 10 s and 7 requests per
  listing at a cap of 50. The run then builds 474 text bodies for a parse.
- **Failure paths** (canned pages, no network): robot check and sign-in page each stop the walk
  with one note and no browser; an unrecognisable page fails that ASIN only; a missing cookie
  file is reported before any request.
- **Not exercised live:** the Sign in to Amazon window end to end (the headed browser launches
  from `.venv`; an actual sign-in was not performed), a real robot check, and a parse of the new
  reviews through OpenAI.
- **Cost to know:** reviews per listing went from ~9 to ~45, and every review is one tagging
  call, so a parse costs about five times what it did. `Reviews per listing` in Settings is the dial.

## Customer photos in the results view (built 2026-11-11)

- **Where they come from.** `results.py` reads each review's `image_urls` from
  `raw/<ASIN>.amazon_reviews.json` when the results are built (`_attach_review_media`), so parses
  made before this get them with no re-extract. `bodies[id].review` gains `photos` and `url`
  (`https://www.<site>/gp/customer-reviews/<id>`). Rainforest and pasted reviews have neither.
- **Why saved links looked broken.** 136 of 838 saved links were Amazon's lazy-load placeholder
  (`grey-pixel.gif`), and many photos were saved under two addresses. A photo is kept only if it
  matches `/images/I/<id>.<ext>`, rewritten to its plain address and kept once per id. After that,
  all 408 photos in the decorative-books parse returned a real JPEG. In the browser, a customer
  photo that errors or is under 40 px wide is dropped for the session (`badPhotos`, `dropPhoto`).
- **Chips.** `_photos_first` sets `photos` on each review chip (mentions whose review has photos),
  puts those mentions first in the chip, and sorts a cluster's review chips photos-first, then by
  count. The chip shows a small outline camera in its own text colour. A collapsed card's preview
  stays the three most-said tags, ordered the same way. 574 of 971 review chips in decorative-books
  carry the marker.
- **Not shown:** review videos (2.5% of reviews); the review link covers them.
- The pinned dark card and the two-step route to the image view that this section first shipped
  with were replaced the same day by the photo view below.

## Photo view: a chip click opens the customer photos (built 2026-11-11)

A click on a cluster chip opens the white image view directly; the dark pinned card is gone
(hover tooltip unchanged, keywords still hover-only, Extract-page image view untouched).

- **Scope is the chip's cluster.** Every review in the cluster with at least one photo, one item
  per review (`buildPhotoScope`, from the cluster's full `review_tags`, whatever the source toggle
  or search shows). Chips carry `data-sec` + `data-cid` to find their cluster.
- **Order.** Grouped by product: products with the most photo reviews for the clicked chip first,
  then by photo reviews in the cluster; inside a product the clicked chip's reviews lead. The
  view opens on the first.
- **Navigation.** On-screen ‹ › step images and roll into the neighbouring review; ← → step
  reviews and flow into the next product; ↑ ↓ step products (the list opens for a moment).
- **Layout.** A strip of product thumbnails left of the stage under a `Products` label; hover (or
  a tap on the label at phone width) opens it into a list with title, rating, ratings count and
  photo-review count. The rail holds the product title and rating, reviewer · stars · date ·
  verified, the review title, the tag being marked, the full text with its sentence highlighted,
  `view on Amazon`, and that review's thumbnails only. No listing images are mixed in.
- **Which sentence.** The clicked chip's if the review has it, else the review's first tag in the
  cluster; the tag is named in a chip above the text.
- **A chip with no photos** (listing chips, review chips without the camera) opens the same view
  over its own sources only, with the product's listing images on the stage.
- **Code.** A third mode of the `#zoom` shell (`zoomState.rv`, beside the Extract page's `list`):
  `openPhotoView`, `rvShow` / `rvFill`, `rvStepImage` / `rvStepReview` / `rvStepProduct`.
  `dropPhoto` removes a broken photo from the open view, and a review left with none.
- **A failing connection is not bad photos.** Amazon's image host fails requests in bursts when
  hit hard (seen while testing), and dropping each failure emptied the view review by review
  until it closed. `photoFailed` drops the first two failures in a row; from the third, with no
  photo loading in between, nothing is dropped and those two are forgiven. The stage then says
  "image didn't load" and paging carries on.
  `results.py` adds `review.author` from the scrape's `username`; nothing else changed there.
- Verified with Playwright at 1280 and 600 px (33/33 each) on decorative-books, plus an older
  parse with no scraped photos opening in the listing-image form.
- **Known:** some scraped review text carries Amazon's "The media could not be loaded." line.
