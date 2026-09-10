"""
The extraction run: fetch every ASIN, normalize it, and write the run folder.

Everything Rainforest returns is kept verbatim under raw/ so a payload change or
a parsing mistake can always be re-examined against the original.
"""

from __future__ import annotations

import time

import config
from pipeline import normalize, rainforest, storage


def run_extraction(job, slug: str, run_id: str, items: list[dict], domain: str) -> dict:
    """
    Fetch and normalize every ASIN in `items`.

    Reports progress through `job` as each ASIN lands, and returns the summary
    that the UI shows in its stat strip.
    """
    started = time.perf_counter()
    directory = storage.run_dir(slug, run_id)
    raw_dir = directory / "raw"

    job.set_total(len(items))
    job.step(label=f"Fetching {len(items)} listings from Rainforest…", count=0)

    fetched: list[dict] = []
    credits_left = None

    def on_result(result: dict) -> None:
        # Keep the raw payload before touching it.
        if result.get("product"):
            storage.write_json(raw_dir / f"{result['asin']}.product.json", result["product"])
        if result.get("extra_reviews"):
            storage.write_json(raw_dir / f"{result['asin']}.reviews.json", result["extra_reviews"])

        if result.get("error"):
            job.fail_item(result["asin"], result["error"])

        fetched.append(result)
        job.step(label=f"Fetched {len(fetched)} of {len(items)} listings")

    results = rainforest.fetch_all(items, domain, on_result=on_result, on_note=job.note)

    for result in results:
        if result.get("product"):
            credits_left = rainforest.credits_remaining(result["product"]) or credits_left

    # Rainforest returns reviews only intermittently, so some ASINs cost more
    # than one call. Say so plainly rather than letting the credit count drift.
    api_calls = sum(r.get("attempts") or 1 for r in results)
    retries = api_calls - len(results)
    if retries > 0:
        job.note(
            f"Rainforest left the reviews out of {retries} "
            f"{'response' if retries == 1 else 'responses'}, so we asked again "
            f"({retries} extra {'credit' if retries == 1 else 'credits'})."
        )

    job.step(label="Organising the results…", count=0)
    products = [normalize.normalize_product(r) for r in results]
    summary = normalize.summarize(products)

    stubborn = [p["asin"] for p in products if p.get("fetch_ok") and not p.get("reviews")]
    if stubborn:
        shown = ", ".join(stubborn[:5]) + (f" and {len(stubborn) - 5} more" if len(stubborn) > 5 else "")
        job.note(
            f"Rainforest never returned reviews for {len(stubborn)} "
            f"{'listing' if len(stubborn) == 1 else 'listings'} ({shown}) after "
            f"{config.PRODUCT_ATTEMPTS} tries. Their bullets are still usable."
        )

    extracted = {
        "group": slug,
        "run_id": run_id,
        "amazon_domain": domain,
        "fetched_at": storage.now_iso(),
        "review_source": "product listing only" if not config.TRY_REVIEWS_ENDPOINT else "auto",
        "products": products,
    }
    storage.write_json(directory / "extracted.json", extracted)

    if not (directory / "edits.json").exists():
        storage.write_json(directory / "edits.json", {})

    log = {
        "run_id": run_id,
        "started_at": storage.now_iso(),
        "state": "done",
        "amazon_domain": domain,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "credits_remaining": credits_left,
        "api_calls": api_calls,
        "review_retries": retries,
        "notes": list(job.notes),
        "failures": list(job.failures),
        "inputs": items,
        **summary,
    }
    storage.write_json(directory / "run_log.json", log)

    return {"run_id": run_id, "slug": slug, **summary,
            "credits_remaining": credits_left,
            "elapsed_seconds": log["elapsed_seconds"]}
