"""
Turn raw Rainforest payloads into the stable `extracted.json` shape.

Everything downstream reads this schema, never the raw payload, so a change in
Rainforest's response only has to be absorbed here. Field paths and the
defensive isinstance guards come from amazon_web_scrape.py.
"""

from __future__ import annotations

import re

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


def parse_pasted_reviews(text: str, existing: list[dict] | None = None) -> list[dict]:
    """Split a pasted block into reviews — one blank line between each.

    Rainforest never returns reviews for some listings, so pasting is the only
    route for those. Whatever is pasted becomes the body; title and rating are
    left empty for you to fill in inline if you care. Both are optional
    downstream: tagging joins title and body, so an empty title costs nothing.
    """
    taken = {r.get("id") for r in (existing or [])}
    reviews: list[dict] = []
    counter = 1

    for block in re.split(r"\n\s*\n", text or ""):
        body = "\n".join(clean_text(line) for line in block.splitlines()).strip()
        if len(body) < config.MIN_REVIEW_CHARS:
            continue

        # Manual ids have to stay unique within the product, and compaction
        # merges reviews across runs, so never reuse one that already exists.
        while f"manual_{counter}" in taken:
            counter += 1
        review_id = f"manual_{counter}"
        taken.add(review_id)

        reviews.append({
            "id": review_id,
            "title": "",
            "body": body,
            "rating": None,
            "date": None,
            "verified_purchase": False,
            "helpful_votes": 0,
            "source": "manual",
        })

    return reviews


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
