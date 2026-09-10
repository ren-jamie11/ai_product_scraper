"""
Rainforest API client.

Two request types matter here:

  type=product  Always works. Returns the listing plus ~8-9 `top_reviews`.
  type=reviews  Returns HTTP 503 "temporarily unavailable" as of 2026-09-10.
                We still check once per run in case it comes back, then fall
                back to the product call's reviews and say so.

Built on the request shape in amazon_web_scrape.py, with failures reported
per-ASIN instead of printed and dropped.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config

ENDPOINT = "https://api.rainforestapi.com/request"


class RainforestError(Exception):
    """A fetch failure, phrased for the user."""


def _friendly_http_error(status: int, asin: str, domain: str, body_text: str) -> str:
    if status in (401, 403):
        return "Rainforest rejected the API key. Check RAINFOREST_API_KEY in config.py."
    if status == 404:
        return f"{asin} wasn't found on {domain} — check the URL."
    if status == 429:
        return "Rainforest is rate limiting us. Try again in a minute."
    if status >= 500:
        return f"Rainforest had a server problem (HTTP {status}). Try again shortly."
    return f"Rainforest returned HTTP {status}: {body_text[:160]}"


def _check_payload(body: dict, asin: str, domain: str) -> dict:
    """Rainforest can return HTTP 200 with success=false. Treat that as an error."""
    info = body.get("request_info") or {}
    if info.get("success") is False:
        message = info.get("message") or "Rainforest declined the request."
        if "credit" in message.lower():
            raise RainforestError("Your Rainforest credits are used up.")
        raise RainforestError(f"Rainforest declined {asin} on {domain}: {message}")
    return body


def request(params: dict, asin: str = "", domain: str = "") -> dict:
    """One Rainforest call. Raises RainforestError with a readable message."""
    query = {
        "api_key": config.RAINFOREST_API_KEY,
        "output": "json",
        "amazon_domain": domain or config.AMAZON_DOMAIN,
        **params,
    }
    try:
        res = requests.get(ENDPOINT, params=query, timeout=45)
    except requests.Timeout:
        raise RainforestError(f"Rainforest timed out fetching {asin}.")
    except requests.RequestException as exc:
        raise RainforestError(f"Couldn't reach Rainforest: {exc}")

    if not res.ok:
        # A 503 on the reviews type carries a useful message in the body.
        try:
            info = (res.json().get("request_info") or {})
            if info.get("message"):
                raise RainforestError(info["message"])
        except (ValueError, AttributeError):
            pass
        raise RainforestError(_friendly_http_error(res.status_code, asin, query["amazon_domain"], res.text))

    try:
        body = res.json()
    except ValueError:
        raise RainforestError(f"Rainforest sent back something that wasn't JSON for {asin}.")

    return _check_payload(body, asin, query["amazon_domain"])


def fetch_product(asin: str, domain: str) -> dict:
    """The listing payload: title, bullets, images, price, and top_reviews."""
    return request({"type": "product", "asin": asin}, asin=asin, domain=domain)


def reviews_endpoint_status(asin: str, domain: str) -> tuple[bool, str]:
    """
    Ask once per run whether type=reviews is usable.

    Rainforest does not charge for the 503, so this check is free when the
    endpoint is down and costs one credit when it works.
    """
    if not config.TRY_REVIEWS_ENDPOINT:
        return False, "Extra review pages are turned off in config.py."
    try:
        body = request({"type": "reviews", "asin": asin}, asin=asin, domain=domain)
    except RainforestError as exc:
        return False, f"Rainforest's reviews endpoint is unavailable ({exc}). Using the reviews from each listing instead."
    if not (body.get("reviews") or []):
        return False, "Rainforest's reviews endpoint returned nothing. Using the reviews from each listing instead."
    return True, "Rainforest's reviews endpoint is available."


def fetch_review_pages(asin: str, domain: str, target: int) -> list[dict]:
    """Page through type=reviews until we hit `target` or stop getting new ones."""
    collected: list[dict] = []
    seen: set[str] = set()

    for page in range(1, 11):  # Amazon itself never goes past 10 pages
        if len(collected) >= target:
            break
        try:
            body = request({"type": "reviews", "asin": asin, "page": page}, asin=asin, domain=domain)
        except RainforestError:
            break

        fresh = [r for r in (body.get("reviews") or [])
                 if isinstance(r, dict) and r.get("id") and r["id"] not in seen]
        if not fresh:
            break
        for review in fresh:
            seen.add(review["id"])
        collected.extend(fresh)

    return collected[:target]


def fetch_product_persistent(asin: str, domain: str) -> tuple[dict, list[dict], int]:
    """
    Fetch a product, asking again if the reviews didn't come through.

    Rainforest's product call includes `top_reviews` only some of the time — the
    same ASIN answers with 9 reviews on one call and 0 on the next. Retrying is
    the only way to get reliable review coverage, so we retry precisely when it
    can help: the listing has ratings, but this call returned no reviews.

    Returns (best_payload, extra_reviews_from_retries, attempts_used).
    """
    best: dict | None = None
    best_keys = -1
    reviews: dict[str, dict] = {}
    attempts = 0

    for _ in range(max(1, config.PRODUCT_ATTEMPTS)):
        attempts += 1
        body = fetch_product(asin, domain)
        product = body.get("product") or {}

        # Keep the richest payload we've seen, not necessarily the last.
        if len(product.keys()) > best_keys:
            best, best_keys = body, len(product.keys())

        for raw in (product.get("top_reviews") or []):
            if isinstance(raw, dict) and raw.get("id"):
                reviews.setdefault(raw["id"], raw)

        if reviews:
            break                       # got what we came for
        if not product.get("title"):
            break                       # dead ASIN; retrying won't conjure one
        if not product.get("ratings_total"):
            break                       # genuinely has no reviews to return
        time.sleep(0.4)

    in_best = {r.get("id") for r in ((best or {}).get("product") or {}).get("top_reviews") or []}
    extra = [r for rid, r in reviews.items() if rid not in in_best]
    return best or {}, extra, attempts


def _fetch_one(item: dict, domain: str, use_reviews_endpoint: bool) -> dict:
    """Fetch everything for a single ASIN. Never raises — failure is data."""
    asin = item["asin"]
    started = time.perf_counter()
    result = {
        "asin": asin,
        "source_url": item.get("source_url"),
        "product": None,
        "extra_reviews": [],
        "attempts": 0,
        "error": None,
        "seconds": 0.0,
    }
    try:
        product, extra, attempts = fetch_product_persistent(asin, domain)
        result["product"] = product
        result["extra_reviews"] = extra
        result["attempts"] = attempts
        if use_reviews_endpoint:
            result["extra_reviews"] += fetch_review_pages(asin, domain, config.REVIEW_TARGET)
    except RainforestError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad ASIN must not end the run
        result["error"] = f"Unexpected problem fetching {asin}: {exc}"

    result["seconds"] = round(time.perf_counter() - started, 2)
    return result


def fetch_all(items: list[dict], domain: str, on_result=None, on_note=None) -> list[dict]:
    """
    Fetch every ASIN in parallel.

    `on_result(result)` fires as each one lands so the UI can count up;
    `on_note(text)` reports run-wide observations such as the reviews endpoint
    being down. Results come back in the same order as `items`.
    """
    if not items:
        return []

    available, note = reviews_endpoint_status(items[0]["asin"], domain)
    if on_note:
        on_note(note)

    results: dict[str, dict] = {}
    workers = max(1, min(len(items), config.RAINFOREST_WORKERS))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, item, domain, available): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {"asin": item["asin"], "source_url": item.get("source_url"),
                          "product": None, "extra_reviews": [],
                          "error": f"Unexpected problem: {exc}", "seconds": 0.0}
            results[result["asin"]] = result
            if on_result:
                on_result(result)

    return [results[i["asin"]] for i in items if i["asin"] in results]


def credits_remaining(payload: dict) -> int | None:
    info = (payload or {}).get("request_info") or {}
    return info.get("credits_remaining")
