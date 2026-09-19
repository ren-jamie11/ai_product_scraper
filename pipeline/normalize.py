"""
Turn raw Rainforest payloads into the stable `extracted.json` shape.

Everything downstream reads this schema, never the raw payload, so a change in
Rainforest's response only has to be absorbed here. Field paths and the
defensive isinstance guards come from amazon_web_scrape.py.
"""

from __future__ import annotations

import re
from datetime import datetime

import config

# Same ranges as concatenate_reviews() in amazon_web_scrape.py. Emoji survive
# JSON fine but add noise and tokens to every tagging call.
_EMOJI = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F9FF"
    "\U00002700-\U000027BF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002500-\U00002BEF"
    "\U00010000-\U0010FFFF"
    "]+",
    flags=re.UNICODE,
)


def clean_text(value) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[ \t]+", " ", _EMOJI.sub("", value)).strip()


def _dig(obj, *path, default=None):
    """Walk nested dicts without exploding on a missing or wrong-typed level."""
    for key in path:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    return obj if obj is not None else default


def _price(product: dict) -> dict | None:
    price = _dig(product, "buybox_winner", "price")
    if not isinstance(price, dict):
        return None
    return {
        "raw": price.get("raw"),
        "value": price.get("value"),
        "currency": price.get("currency"),
    }


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def parse_price(raw: str, previous: dict | None = None) -> dict | None:
    """Turn a hand-typed price back into the {raw, value, currency} shape.

    The number is a convenience, not the truth — the raw string is what gets
    shown. So an unparseable price is kept verbatim with a null value rather
    than rejected, and the currency carries over from whatever we fetched.
    """
    raw = clean_text(raw)
    if not raw:
        return None

    match = _NUMBER.search(raw.replace(",", ""))
    try:
        value = float(match.group()) if match else None
    except ValueError:
        value = None

    return {
        "raw": raw,
        "value": value,
        "currency": (previous or {}).get("currency") or "USD",
    }


def _images(product: dict, limit: int = 8) -> list[str]:
    images = product.get("images")
    if not isinstance(images, list):
        return []
    links = [i.get("link") for i in images if isinstance(i, dict) and i.get("link")]
    return links[:limit]


def _specifications(product: dict, limit: int = 25) -> list[dict]:
    specs = product.get("specifications")
    if not isinstance(specs, list):
        return []
    return [
        {"name": clean_text(s.get("name")), "value": clean_text(s.get("value"))}
        for s in specs[:limit]
        if isinstance(s, dict) and s.get("name")
    ]


def _feature_bullets(product: dict) -> list[str]:
    bullets = product.get("feature_bullets")
    if not isinstance(bullets, list):
        return []
    return [clean_text(b) for b in bullets if isinstance(b, str) and b.strip()]


def normalize_review(raw: dict, source: str) -> dict | None:
    """One review, or None if it carries no usable text."""
    if not isinstance(raw, dict):
        return None
    body = clean_text(raw.get("body"))
    if len(body) < config.MIN_REVIEW_CHARS:
        return None

    utc = _dig(raw, "date", "utc")
    return {
        "id": raw.get("id"),
        "title": clean_text(raw.get("title")),
        "body": body,
        "rating": raw.get("rating"),
        "date": utc[:10] if isinstance(utc, str) else None,
        "verified_purchase": bool(raw.get("verified_purchase")),
        "helpful_votes": raw.get("helpful_votes") or 0,
        "source": source,
    }


# --- Reviews pasted straight from Amazon ------------------------------------
#
# Both the product page and the "all reviews" page paste as the same shape:
#
#     Reviewer Name
#     5 out of 5 starsTitle            (review page: "5.0 out of 5 stars Title")
#     Reviewed in the United States on September 7, 2026
#     Color: BrownSize: 8.7" x 3.6"    (optional, may end in "Verified Purchase")
#     Verified Purchase                (optional)
#     Body, possibly several paragraphs
#     Title                            (product page echoes it, sometimes twice)
#     2 people found this helpful
#     Helpful
#     Report
#
# The rating line followed by a "Reviewed in" line is the anchor; everything
# else is read relative to it.

_RATING_LINE = re.compile(r"^(\d(?:\.\d)?) out of 5 stars\s*(.*)$", re.IGNORECASE)
_REVIEWED_LINE = re.compile(r"^Reviewed in\b", re.IGNORECASE)
_REVIEWED_DATE = re.compile(r"\bon\s+(.+?)\s*$")
_HELPFUL = re.compile(r"^(One|\d[\d,]*) (?:person|people) found this helpful$", re.IGNORECASE)
_FOOTER = re.compile(
    r"^(?:helpful|report|read more|customer (?:image|video)s?|translate (?:all )?reviews? to \w+"
    r"|see (?:more|all) reviews|show \d+ more reviews?|top reviews from .*"
    r"|from the united states|from other countries"
    r"|(?:one|\d[\d,]*) (?:person|people) found this helpful)$",
    re.IGNORECASE,
)
# Lines between the "Reviewed in" line and the body that carry no review text.
_META_LINE = re.compile(
    r"^(?:verified purchase|vine customer review of free product|click to play video)$",
    re.IGNORECASE,
)
_VARIATION_KEYS = re.compile(
    r"^(?:colou?r|size|style|pattern|material|scent|shape|design|flavou?r|capacity|finish"
    r"|configuration|edition|length|wattage|number of items|item package quantity)"
    r"(?: name)?:", re.IGNORECASE,
)
_ANY_KEY = re.compile(r"[A-Za-z][A-Za-z ]{0,30}:\s*\S")
# Reviewer badges Amazon prints between the name and the rating line.
_BADGE = re.compile(r"^(?:vine voice|top \d+ reviewer|hall of fame|top contributor\b.*)$",
                    re.IGNORECASE)


def _is_variation(line: str) -> bool:
    """`Color: BrownSize: 8.7"` — but not a body that opens with `Update: ...`.

    A known variation key is enough. An unknown key only counts when the line
    also carries a second key or the glued-on "Verified Purchase", so a review
    that starts with "Update:" or "Edit:" keeps its first line.
    """
    if _VARIATION_KEYS.match(line):
        return True
    return bool(_ANY_KEY.match(line)) and (
        len(_ANY_KEY.findall(line)) > 1 or line.lower().endswith("verified purchase")
    )


def _parse_date(line: str) -> str | None:
    match = _REVIEWED_DATE.search(line)
    if not match:
        return None
    for fmt in ("%B %d, %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(match.group(1), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def body_key(body: str) -> str:
    """What makes two reviews the same review: the body, ignoring case and spacing."""
    return re.sub(r"\s+", " ", body or "").strip().casefold()


def find_duplicate(key: str, seen: dict[str, str]) -> tuple[str | None, str | None]:
    """Is this body already here? `seen` maps a body key to whatever owns it.

    The owner is a review id when pasting and a body_id at parse time — the
    caller decides, this only reports the match.

    An exact match is `same`. Beyond that, the product page truncates long
    reviews at "Read more" while the reviews page carries them whole, so the
    same review pasted from both places differs only by its tail: one key is a
    prefix of the other. That counts as the same review once both are at least
    DUP_PREFIX_MIN_CHARS long, reported from the new key's point of view —
    `extends` when it is the fuller copy, `truncated` when it is the cut one.
    """
    if not key:
        return None, None
    if key in seen:
        return seen[key], "same"

    for other, owner in seen.items():
        if min(len(key), len(other)) < config.DUP_PREFIX_MIN_CHARS:
            continue
        if key.startswith(other):
            return owner, "extends"
        if other.startswith(key):
            return owner, "truncated"
    return None, None


def _next_filled(lines: list[str], start: int) -> int | None:
    for i in range(start, len(lines)):
        if lines[i]:
            return i
    return None


def _review_starts(lines: list[str]) -> list[tuple[int, int]]:
    """(first line of the review's header, rating-line index) for every review.

    A rating line only counts when the next filled line is "Reviewed in", so a
    body sentence like "5 out of 5 stars from me" never starts a new review.
    The header reaches back over the reviewer's name and any badge lines, so
    none of them leak into the end of the previous review's body.
    """
    starts = []
    for i, line in enumerate(lines):
        if not _RATING_LINE.match(line):
            continue
        nxt = _next_filled(lines, i + 1)
        if nxt is None or not _REVIEWED_LINE.match(lines[nxt]):
            continue

        head = i
        j = i - 1
        while j >= 0 and not lines[j]:
            j -= 1
        while j >= 0 and _BADGE.match(lines[j]):
            head, j = j, j - 1
        if j >= 0 and lines[j] and not _FOOTER.match(lines[j]) and not _RATING_LINE.match(lines[j]):
            head = j
        starts.append((head, i))
    return starts


def _parse_amazon_review(lines: list[str]) -> dict:
    """One review's lines, from the rating line up to the next reviewer's name."""
    rating_match = _RATING_LINE.match(lines[0])
    rating = float(rating_match.group(1))
    title = rating_match.group(2).strip()

    pos = _next_filled(lines, 1)
    date = _parse_date(lines[pos])
    pos += 1

    verified = False
    variation_seen = False
    while pos < len(lines):
        line = lines[pos]
        if not line:
            pos += 1
        elif _META_LINE.match(line):
            verified = verified or line.lower() == "verified purchase"
            pos += 1
        elif not variation_seen and _is_variation(line):
            variation_seen = True
            verified = verified or line.lower().endswith("verified purchase")
            pos += 1
        else:
            break

    body_lines: list[str] = []
    while pos < len(lines) and not _FOOTER.match(lines[pos]):
        body_lines.append(lines[pos])
        pos += 1

    helpful = 0
    for line in lines[pos:]:
        match = _HELPFUL.match(line)
        if match:
            count = match.group(1)
            helpful = 1 if count.lower() == "one" else int(count.replace(",", ""))
            break

    # The product page repeats the title under the body, sometimes twice. A
    # body that *is* the title ("Love" / "Love") keeps its one line.
    title_key = body_key(title)
    while body_lines and not body_lines[-1]:
        body_lines.pop()
    while (title_key and body_key(body_lines[-1] if body_lines else "") == title_key
           and sum(1 for line in body_lines if line) > 1):
        body_lines.pop()
        while body_lines and not body_lines[-1]:
            body_lines.pop()

    body = re.sub(r"\n{3,}", "\n\n", "\n".join(body_lines)).strip()
    return {
        "title": title,
        "body": body,
        "rating": int(rating) if rating.is_integer() else rating,
        "date": date,
        "verified_purchase": verified,
        "helpful_votes": helpful,
    }


def parse_pasted_reviews(text: str,
                         existing: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Split a pasted block into reviews.

    Rainforest never returns reviews for some listings, so pasting is the only
    route for those. Text copied from Amazon's product page or reviews page is
    read deterministically: rating, title, date and verified purchase come from
    the header lines, the reviewer name and "Helpful / Report" footers are
    dropped. Text with no Amazon rating lines falls back to one review per
    blank-line-separated block, with the title and rating left empty.

    A review whose body matches one already on the product (fetched or pasted
    earlier), or earlier in the same paste, is skipped — it would otherwise be
    tagged a second time and inflate every count downstream. Matching is
    `find_duplicate`, so a copy truncated at "Read more" and the whole one are
    the same review: the truncated one is dropped, and when the paste is the
    fuller copy the review already on the product is extended to the full text
    rather than joined by a near-twin.

    Returns the new reviews and a report:
    `{"duplicates": n, "extended": [{"id": ..., "body": ...}]}`. Extensions are
    counted as duplicates too — they are copies of a review already here — and
    it is the caller that applies them to the product.
    """
    lines = [clean_text(line) for line in (text or "").replace("\r\n", "\n").split("\n")]
    starts = _review_starts(lines)

    if starts:
        parsed = []
        for n, (_, rating_at) in enumerate(starts):
            end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
            parsed.append(_parse_amazon_review(lines[rating_at:end]))
    else:
        parsed = [
            {"title": "", "body": block.strip(), "rating": None, "date": None,
             "verified_purchase": False, "helpful_votes": 0}
            for block in re.split(r"\n\s*\n", "\n".join(lines))
        ]

    taken = {r.get("id") for r in (existing or [])}
    seen = {body_key(r.get("body")): r.get("id") for r in (existing or [])}
    seen.pop("", None)
    reviews: list[dict] = []
    minted: dict[str, dict] = {}
    report: dict = {"duplicates": 0, "extended": []}
    counter = 1

    for review in parsed:
        if len(review["body"]) < config.MIN_REVIEW_CHARS:
            continue

        key = body_key(review["body"])
        owner, relation = find_duplicate(key, seen)
        if relation:
            report["duplicates"] += 1
            if relation == "extends":
                # The paste carries the whole review and what we hold is the
                # copy Amazon cut off at "Read more". Keep the review — its id
                # is already referenced everywhere — and give it the full text.
                # One pasted this run we can fix outright; one already on the
                # product is the caller's to apply.
                if owner in minted:
                    minted[owner]["body"] = review["body"]
                else:
                    report["extended"].append({"id": owner, "body": review["body"]})
                seen = {k: v for k, v in seen.items() if v != owner}
                seen[key] = owner
            continue

        # Manual ids have to stay unique within the product, and compaction
        # merges reviews across runs, so never reuse one that already exists.
        while f"manual_{counter}" in taken:
            counter += 1
        review_id = f"manual_{counter}"
        taken.add(review_id)
        seen[key] = review_id

        fresh = {"id": review_id, **review, "source": "manual"}
        minted[review_id] = fresh
        reviews.append(fresh)

    return reviews, report


def normalize_product(fetched: dict) -> dict:
    """
    Build one `products[]` entry from a fetch result.

    A failed fetch still produces an entry — with the error in `warnings` — so
    a missing competitor is visible in the UI rather than silently absent.
    """
    asin = fetched.get("asin")
    warnings: list[str] = []

    def failed(reason: str) -> dict:
        return {
            "asin": asin,
            "source_url": fetched.get("source_url"),
            "link": None, "title": None, "brand": None, "price": None,
            "rating": None, "ratings_total": None, "rating_breakdown": None,
            "main_image": None, "images": [],
            "categories_flat": None, "bestsellers_rank_flat": None,
            "material": None, "color": None, "model_number": None,
            "specifications": [], "feature_bullets": [], "reviews": [],
            "fetch_ok": False,
            "warnings": [reason],
        }

    if fetched.get("error"):
        return failed(fetched["error"])

    product = _dig(fetched, "product", "product", default={}) or {}

    # A dead or mistyped ASIN does not 404. Rainforest answers HTTP 200 with
    # success=true and an empty product object, which would otherwise be
    # counted as a successful fetch that simply had no bullets.
    if not product.get("title"):
        domain = _dig(fetched, "product", "request_parameters", "amazon_domain",
                      default=config.AMAZON_DOMAIN)
        return failed(
            f"Rainforest returned no product data for {asin}. Check the ASIN is "
            f"correct and still listed on {domain}."
        )

    reviews: list[dict] = []
    seen_ids: set[str] = set()
    for raw in (product.get("top_reviews") or []):
        review = normalize_review(raw, "product")
        if review and review["id"] not in seen_ids:
            seen_ids.add(review["id"])
            reviews.append(review)
    for raw in (fetched.get("extra_reviews") or []):
        review = normalize_review(raw, "product-retry")
        if review and review["id"] not in seen_ids:
            seen_ids.add(review["id"])
            reviews.append(review)

    bullets = _feature_bullets(product)
    if not bullets:
        warnings.append("Rainforest returned no feature bullets for this listing.")
    if not reviews:
        # storage.load_run drops this note once any reviews exist; keep the two
        # strings identical (storage.NO_REVIEWS_WARNING).
        warnings.append("Rainforest returned no reviews for this listing.")

    return {
        "asin": asin,
        "source_url": fetched.get("source_url"),
        "link": product.get("link"),
        "title": clean_text(product.get("title")) or None,
        "brand": product.get("brand"),
        "price": _price(product),
        "rating": product.get("rating"),
        "ratings_total": product.get("ratings_total"),
        "rating_breakdown": product.get("rating_breakdown"),
        "main_image": _dig(product, "main_image", "link"),
        "images": _images(product),
        "categories_flat": product.get("categories_flat"),
        "bestsellers_rank_flat": product.get("bestsellers_rank_flat"),
        "material": product.get("material"),
        "color": product.get("color"),
        "model_number": product.get("model_number"),
        "specifications": _specifications(product),
        "feature_bullets": bullets,
        "reviews": reviews,
        "fetch_ok": True,
        "warnings": warnings,
    }


def summarize(products: list[dict]) -> dict:
    """The numbers shown in the run summary strip and written to run_log.json."""
    total = len(products)
    ok = [p for p in products if p.get("fetch_ok")]
    with_bullets = [p for p in ok if p.get("feature_bullets")]
    with_reviews = [p for p in ok if p.get("reviews")]
    review_count = sum(len(p.get("reviews") or []) for p in ok)

    # Percentages are over listings we actually fetched, not over everything
    # requested — a dead ASIN is already reported separately as a failure, and
    # counting it here would make good data look patchy.
    def pct(part: int) -> int:
        return round(100 * part / len(ok)) if ok else 0

    return {
        "asins_total": total,
        "asins_ok": len(ok),
        "asins_failed": total - len(ok),
        "with_bullets": len(with_bullets),
        "with_bullets_pct": pct(len(with_bullets)),
        "with_reviews": len(with_reviews),
        "with_reviews_pct": pct(len(with_reviews)),
        "reviews_total": review_count,
        "reviews_avg": round(review_count / len(ok), 1) if ok else 0,
        "bullets_total": sum(len(p.get("feature_bullets") or []) for p in ok),
        "bodies_total": len(with_bullets) + review_count,
    }


# ---------------------------------------------------------------------------
# Text bodies — the unit that gets tagged in Phase 3
# ---------------------------------------------------------------------------

def listing_body(product: dict) -> str:
    """Product title, then each feature bullet, one per line."""
    parts = [product.get("title") or ""] + list(product.get("feature_bullets") or [])
    return "\n".join(p for p in parts if p).strip()


def review_body(review: dict) -> str:
    """Review title, then review body."""
    return "\n".join(p for p in [review.get("title"), review.get("body")] if p).strip()


def build_bodies(products: list[dict]) -> list[dict]:
    """Every taggable text body in a run, with the ids that carry provenance."""
    bodies = []
    for product in products:
        asin = product.get("asin")
        text = listing_body(product)
        if text:
            bodies.append({"body_id": f"{asin}:listing", "asin": asin,
                           "type": "listing", "text": text})
        for review in product.get("reviews") or []:
            text = review_body(review)
            if text:
                bodies.append({"body_id": f"{asin}:review:{review['id']}", "asin": asin,
                               "type": "review", "text": text})
    return bodies
