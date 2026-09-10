"""
One-off probe: how many reviews can Rainforest actually give us per ASIN?

The product call reliably returns ~8 reviews in `top_reviews`. Whether the
separate `type=reviews` request still works, and how far it paginates, can only
be settled by asking. This script asks, and reports exactly what it cost.

    python probe_reviews.py                    # uses a default ASIN
    python probe_reviews.py B08P5LPZFJ         # your own ASIN
    python probe_reviews.py B08P5LPZFJ --max-pages 5 --stars

Each request spends one Rainforest credit. The script prints the credits
remaining after every call so you can stop early if you need to.
"""

from __future__ import annotations

import argparse
import sys

import requests

import config

ENDPOINT = "https://api.rainforestapi.com/request"
DEFAULT_ASIN = "B08HVLJBF6"  # the mug from data templates/rainforest_api_example_output.json

STAR_FILTERS = ["all_stars", "five_star", "four_star", "three_star", "two_star", "one_star"]


def call(params: dict) -> tuple[int, dict]:
    """Make one Rainforest request. Returns (status_code, parsed_body_or_error)."""
    query = {"api_key": config.RAINFOREST_API_KEY, "output": "json", **params}
    try:
        res = requests.get(ENDPOINT, params=query, timeout=40)
    except requests.RequestException as exc:
        return 0, {"_error": str(exc)}
    try:
        return res.status_code, res.json()
    except ValueError:
        return res.status_code, {"_error": res.text[:300]}


def credits(body: dict) -> str:
    info = body.get("request_info") or {}
    left = info.get("credits_remaining")
    used = info.get("credits_used")
    return f"credits used={used} remaining={left}" if left is not None else "credits unknown"


def review_ids(body: dict) -> list[str]:
    reviews = body.get("reviews") or []
    return [r.get("id") for r in reviews if isinstance(r, dict) and r.get("id")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asin", nargs="?", default=DEFAULT_ASIN)
    ap.add_argument("--domain", default=config.AMAZON_DOMAIN)
    ap.add_argument("--max-pages", type=int, default=4, help="how many review pages to try (1 credit each)")
    ap.add_argument("--stars", action="store_true", help="also probe review_stars fan-out (5 more credits)")
    args = ap.parse_args()

    if not config.RAINFOREST_API_KEY:
        print("RAINFOREST_API_KEY is empty in config.py.")
        return 1

    print(f"\nProbing ASIN {args.asin} on {args.domain}\n" + "=" * 64)

    # ---------------------------------------------------------------- product
    print("\n[1] type=product  — the baseline we can always rely on")
    status, body = call({"type": "product", "asin": args.asin, "amazon_domain": args.domain})
    if status != 200:
        print(f"    FAILED  HTTP {status}: {body.get('_error') or body}")
        return 1

    product = body.get("product") or {}
    top = product.get("top_reviews") or []
    print(f"    HTTP 200  {credits(body)}")
    print(f"    title            : {(product.get('title') or '')[:70]}")
    print(f"    ratings_total    : {product.get('ratings_total')}")
    print(f"    feature_bullets  : {len(product.get('feature_bullets') or [])}")
    print(f"    top_reviews      : {len(top)}   <-- guaranteed floor")
    product_ids = {r.get("id") for r in top if isinstance(r, dict) and r.get("id")}

    # ---------------------------------------------------------------- reviews
    print("\n[2] type=reviews  — does the endpoint still exist?")
    status, body = call({"type": "reviews", "asin": args.asin, "amazon_domain": args.domain})
    if status != 200 or "_error" in body:
        print(f"    UNAVAILABLE  HTTP {status}: {str(body.get('_error') or body)[:200]}")
        print(f"\n    VERDICT: reviews endpoint is not usable. Set REVIEW_TARGET to "
              f"{len(top)} and rely on the product call.")
        return 0

    page1 = review_ids(body)
    print(f"    HTTP 200  {credits(body)}")
    print(f"    reviews on page 1: {len(page1)}")
    for key in ("pagination", "summary", "total_reviews"):
        if key in body:
            print(f"    {key:17}: {str(body[key])[:160]}")
    if not page1:
        print("\n    VERDICT: endpoint responds but returns no reviews. Rely on the product call.")
        return 0

    seen = set(page1)

    # ------------------------------------------------------------ pagination
    # Rainforest has used both `page` and `review_page` over time; try each and
    # see which one actually returns different reviews.
    print("\n[3] pagination — which parameter name moves the window?")
    for param in ("page", "review_page"):
        status, body = call({"type": "reviews", "asin": args.asin,
                             "amazon_domain": args.domain, param: 2})
        ids = review_ids(body)
        overlap = len(set(ids) & set(page1))
        verdict = "NO EFFECT (same reviews)" if ids and overlap == len(ids) else \
                  "WORKS (new reviews)" if ids else "empty"
        print(f"    {param:12}=2  HTTP {status}  got {len(ids):>2}  overlap {overlap:>2}  -> {verdict}")
        if ids and overlap < len(ids):
            page_param = param
            seen |= set(ids)
            break
    else:
        print(f"\n    VERDICT: pagination does not advance. Ceiling is ~{len(seen)} reviews per ASIN.")
        page_param = None

    # ------------------------------------------------------------ walk pages
    if page_param:
        print(f"\n[4] walking pages with {page_param!r} (stopping when nothing new arrives)")
        for page in range(3, args.max_pages + 1):
            status, body = call({"type": "reviews", "asin": args.asin,
                                 "amazon_domain": args.domain, page_param: page})
            ids = review_ids(body)
            new = set(ids) - seen
            print(f"    page {page:>2}  HTTP {status}  got {len(ids):>2}  new {len(new):>2}  "
                  f"total unique {len(seen | new):>3}  {credits(body)}")
            if not new:
                print("    -> no new reviews; that's the ceiling.")
                break
            seen |= new

    # ---------------------------------------------------------- star fan-out
    if args.stars:
        print("\n[5] review_stars fan-out — do star filters surface different reviews?")
        for stars in STAR_FILTERS[1:]:
            status, body = call({"type": "reviews", "asin": args.asin,
                                 "amazon_domain": args.domain, "review_stars": stars})
            ids = review_ids(body)
            new = set(ids) - seen
            print(f"    {stars:11}  HTTP {status}  got {len(ids):>2}  new {len(new):>2}")
            seen |= new

    # ---------------------------------------------------------------- verdict
    print("\n" + "=" * 64)
    print(f"  product call alone : {len(product_ids)} reviews")
    print(f"  best total reached : {len(seen)} unique reviews")
    print(f"  page parameter     : {page_param or 'none that works'}")
    print(f"\n  Set REVIEW_TARGET in config.py to at most {len(seen)}.")
    print("=" * 64 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
