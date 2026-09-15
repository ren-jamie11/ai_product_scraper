"""
The extraction run: fetch every ASIN, normalize it, and write the run folder.

Everything Rainforest returns is kept verbatim under raw/ so a payload change or
a parsing mistake can always be re-examined against the original.
"""

from __future__ import annotations

import time

import config
from pipeline import compact, normalize, rainforest, storage


def run_extraction(job, slug: str, run_id: str, items: list[dict], domain: str,
                   to_fetch: list[dict] | None = None) -> dict:
    """
    Build a run out of `items`, fetching only `to_fetch` from Rainforest.

    Anything in `items` but not in `to_fetch` is already held complete
    elsewhere in the group and is carried across instead of bought again. When
    `to_fetch` is omitted every item is fetched, which is what a group with no
    earlier runs amounts to.

    Reports progress through `job` as each ASIN lands, and returns the summary
    that the UI shows in its stat strip.
    """
    started = time.perf_counter()
    directory = storage.run_dir(slug, run_id)
    raw_dir = directory / "raw"

    if to_fetch is None:
        to_fetch = items
    carried = len(items) - len(to_fetch)

    job.set_total(len(to_fetch))
    if to_fetch:
        job.step(label=f"Fetching {len(to_fetch)} listings from Rainforest…", count=0)
    else:
        job.step(label="Everything you pasted is already here…", count=0)

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
        job.step(label=f"Fetched {len(fetched)} of {len(to_fetch)} listings")

    results = rainforest.fetch_all(to_fetch, domain, on_result=on_result, on_note=job.note)

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
    fresh = {p["asin"]: p for p in (normalize.normalize_product(r) for r in results)}

    stubborn = [a for a, p in fresh.items() if p.get("fetch_ok") and not p.get("reviews")]
    if stubborn:
        shown = ", ".join(stubborn[:5]) + (f" and {len(stubborn) - 5} more" if len(stubborn) > 5 else "")
        job.note(
            f"Rainforest never returned reviews for {len(stubborn)} "
            f"{'listing' if len(stubborn) == 1 else 'listings'} ({shown}) after "
            f"{config.PRODUCT_ATTEMPTS} tries. Their bullets are still usable."
        )

    # Fold the fetch into whatever the group already held. An ASIN we skipped
    # comes across whole; one we re-fetched keeps the better of the two records
    # with their reviews merged, so a flaky response can't lose us anything.
    resolved = compact.resolve(slug, items, fresh, run_id)
    compact.copy_raw(slug, resolved["raw_from"], raw_dir)

    if carried:
        job.note(
            f"{carried} of the {len(items)} ASINs {'was' if carried == 1 else 'were'} "
            f"already complete in this group, so {'it was' if carried == 1 else 'they were'} "
            f"carried across instead of fetched again "
            f"({carried} {'credit' if carried == 1 else 'credits'} saved)."
        )

    storage.write_json(directory / "extracted.json", {
        "group": slug,
        "run_id": run_id,
        "amazon_domain": domain,
        "fetched_at": storage.now_iso(),
        "review_source": "product listing only" if not config.TRY_REVIEWS_ENDPOINT else "auto",
        "products": resolved["products"],
    })
    storage.write_json(directory / "edits.json", resolved["edits"])

    # Summarize the run as it now reads — carried ASINs and merged reviews
    # included — rather than only what this fetch returned.
    summary = normalize.summarize(storage.load_run(slug, run_id).get("products", []))

    log = {
        "run_id": run_id,
        "started_at": storage.now_iso(),
        "state": "done",
        "amazon_domain": domain,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "credits_remaining": credits_left,
        "api_calls": api_calls,
        "review_retries": retries,
        "fetched": len(to_fetch),
        "carried": carried,
        "outcomes": resolved["outcomes"],
        "notes": list(job.notes),
        "failures": list(job.failures),
        "inputs": items,
        **summary,
    }
    storage.write_json(directory / "run_log.json", log)

    return {"run_id": run_id, "slug": slug, **summary,
            "credits_remaining": credits_left,
            "elapsed_seconds": log["elapsed_seconds"]}
