"""
Rainforest API client.

Rainforest supplies the listing: title, bullets, images, price, specifications.
It no longer returns reviews (measured 2026-11-10: 45 calls, 0 reviews), so those
come from Amazon directly — see pipeline/amazon_reviews.py. Any `top_reviews` a
product call does carry are still kept by the normalizer.

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


def fetch_product_persistent(asin: str, domain: str) -> tuple[dict, int]:
    """
    Fetch a listing, asking again only when the call fails or comes back empty.

    One call is enough almost every time. A dead ASIN answers with an empty
    product rather than an error, so it uses every attempt before being reported
    — the price of not mistaking a blip for a dead listing.

    Returns (payload, attempts_used). Raises the last error if every attempt failed.
    """
    attempts = max(1, config.PRODUCT_ATTEMPTS)
    body: dict = {}
    error: RainforestError | None = None

    for attempt in range(1, attempts + 1):
        try:
            body, error = fetch_product(asin, domain), None
        except RainforestError as exc:
            error = exc
            # A bad key or spent credits won't improve by asking again.
            if "API key" in str(exc) or "credits" in str(exc):
                raise
        else:
            if (body.get("product") or {}).get("title"):
                return body, attempt
        if attempt < attempts:
            time.sleep(0.6 * attempt)

    if error:
        raise error
    return body, attempts


def _fetch_one(item: dict, domain: str) -> dict:
    """Fetch the listing for a single ASIN. Never raises — failure is data."""
    asin = item["asin"]
    started = time.perf_counter()
    result = {
        "asin": asin,
        "source_url": item.get("source_url"),
        "product": None,
        "attempts": 0,
        "error": None,
        "seconds": 0.0,
    }
    try:
        result["product"], result["attempts"] = fetch_product_persistent(asin, domain)
    except RainforestError as exc:
        result["error"] = str(exc)
        result["attempts"] = max(1, config.PRODUCT_ATTEMPTS)
    except Exception as exc:  # noqa: BLE001 - one bad ASIN must not end the run
        result["error"] = f"Unexpected problem fetching {asin}: {exc}"

    result["seconds"] = round(time.perf_counter() - started, 2)
    return result


def fetch_all(items: list[dict], domain: str, on_result=None) -> list[dict]:
    """
    Fetch every ASIN's listing in parallel.

    `on_result(result)` fires as each one lands so the UI can count up. Results
    come back in the same order as `items`.
    """
    if not items:
        return []

    results: dict[str, dict] = {}
    workers = max(1, min(len(items), config.RAINFOREST_WORKERS))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, item, domain): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {"asin": item["asin"], "source_url": item.get("source_url"),
                          "product": None, "attempts": 1,
                          "error": f"Unexpected problem: {exc}", "seconds": 0.0}
            results[result["asin"]] = result
            if on_result:
                on_result(result)

    return [results[i["asin"]] for i in items if i["asin"] in results]


def credits_remaining(payload: dict) -> int | None:
    info = (payload or {}).get("request_info") or {}
    return info.get("credits_remaining")
