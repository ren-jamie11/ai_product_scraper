"""
The extraction run: fetch every ASIN, normalize it, and write the run folder.

Two sources, side by side. Rainforest supplies each listing (title, bullets,
images, price); Amazon's own review pages supply the reviews, read with the saved
login. Everything either returns is kept verbatim under raw/ so a payload change
or a parsing mistake can always be re-examined against the original.
"""

from __future__ import annotations

import threading
import time

from pipeline import amazon_reviews, compact, normalize, rainforest, settings, storage


# Run notes that stop being true once reviews have been fetched after the fact:
# an earlier fetch that stopped, and what runs from before November 2026 said
# about Rainforest's reviews.
STALE_REVIEW_NOTES = (
    "Amazon sign-in for", "Amazon asked for a robot check",
    "Rainforest's reviews endpoint", "Rainforest left the reviews out",
    "Rainforest never returned reviews",
)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _walk_reviews(job, raw_dir, asins: list[str], domain: str, on_step) -> dict:
    """
    Fetch reviews for each ASIN in turn, on one Amazon session.

    One at a time on purpose: Amazon answers speed with a robot check, and a
    robot check ends the fetch for every ASIN still waiting. A lapsed sign-in
    ends it the same way. Either is reported once, as a note, and the ASINs not
    reached are left for the Fetch reviews button.

    `on_step()` fires once per ASIN, reached or not, so progress always completes.
    Returns the reviews per ASIN (already in the app's shape) and what happened.
    """
    out = {"source": "amazon", "site": domain, "wanted": len(asins), "fetched": 0,
           "failed": 0, "requests": 0, "stopped": None, "by_asin": {}}
    cap = settings.resolve("reviews_per_asin")
    session = None

    def stop(kind: str, message: str, reached: int) -> None:
        out["stopped"] = kind
        job.note(message)
        for _ in asins[reached:]:
            on_step()

    try:
        try:
            session = amazon_reviews.Session(domain)
        except amazon_reviews.SignInNeeded as exc:
            stop("signin", str(exc), 0)
            return out

        for i, asin in enumerate(asins):
            try:
                payload = amazon_reviews.fetch_reviews(session, asin, cap)
            except amazon_reviews.SignInNeeded as exc:
                stop("signin", str(exc), i)
                break
            except amazon_reviews.RobotCheck:
                stop("robot", f"Amazon asked for a robot check after {i} of "
                              f"{_plural(len(asins), 'listing', 'listings')}. Wait a "
                              f"while, then press Fetch reviews for the rest.", i)
                break
            except amazon_reviews.ReviewError as exc:
                out["failed"] += 1
                job.fail_item(asin, str(exc))
                on_step()
                continue
            except Exception as exc:  # noqa: BLE001 - one bad ASIN must not end the run
                out["failed"] += 1
                job.fail_item(asin, f"Unexpected problem reading reviews for {asin}: {exc}")
                on_step()
                continue

            storage.write_json(raw_dir / f"{asin}.amazon_reviews.json", payload)
            out["by_asin"][asin] = amazon_reviews.to_reviews(payload)
            out["fetched"] += 1
            on_step()
    finally:
        if session:
            out["requests"] = session.requests
            session.close()
    return out


def _review_log(walk: dict) -> dict:
    return {k: v for k, v in walk.items() if k != "by_asin"}


def run_extraction(job, slug: str, run_id: str, items: list[dict], domain: str,
                   to_fetch: list[dict] | None = None) -> dict:
    """
    Build a run out of `items`, fetching only `to_fetch`.

    Anything in `items` but not in `to_fetch` is already held complete
    elsewhere in the group and is carried across instead of bought again. When
    `to_fetch` is omitted every item is fetched, which is what a group with no
    earlier runs amounts to.

    Listings and reviews are fetched at the same time: listings in parallel from
    Rainforest, reviews one ASIN after another from Amazon. Reports progress
    through `job` as each lands, and returns the summary that the UI shows in
    its stat strip.
    """
    started = time.perf_counter()
    directory = storage.run_dir(slug, run_id)
    raw_dir = directory / "raw"

    if to_fetch is None:
        to_fetch = items
    carried = len(items) - len(to_fetch)
    n = len(to_fetch)

    job.set_total(2 * n)
    if not to_fetch:
        job.step(label="Everything you pasted is already here…", count=0)

    counts = {"listings": 0, "reviews": 0}
    lock = threading.Lock()

    def progress(which: str) -> None:
        with lock:
            counts[which] += 1
            label = f"Listings {counts['listings']}/{n} · Reviews {counts['reviews']}/{n}"
        job.step(label=label)

    credits_left = None

    def on_result(result: dict) -> None:
        # Keep the raw payload before touching it.
        if result.get("product"):
            storage.write_json(raw_dir / f"{result['asin']}.product.json", result["product"])
        if result.get("error"):
            job.fail_item(result["asin"], result["error"])
        progress("listings")

    walk: dict = {"source": "amazon", "site": domain, "wanted": 0, "fetched": 0,
                  "failed": 0, "requests": 0, "stopped": None, "by_asin": {}}

    def review_worker() -> None:
        walk.update(_walk_reviews(job, raw_dir, [i["asin"] for i in to_fetch], domain,
                                  lambda: progress("reviews")))

    reviewer = threading.Thread(target=review_worker, daemon=True, name=f"reviews-{run_id}")
    if to_fetch:
        job.step(label=f"Listings 0/{n} · Reviews 0/{n}", count=0)
        reviewer.start()

    results = rainforest.fetch_all(to_fetch, domain, on_result=on_result)

    if reviewer.is_alive():
        job.step(label=f"Listings {n}/{n} · Reviews {counts['reviews']}/{n}", count=0)
    if to_fetch:
        reviewer.join()

    for result in results:
        if result.get("product"):
            credits_left = rainforest.credits_remaining(result["product"]) or credits_left
        result["amazon_reviews"] = walk["by_asin"].get(result["asin"]) or []

    api_calls = sum(r.get("attempts") or 1 for r in results)
    retries = api_calls - len(results)
    if retries > 0:
        job.note(f"Rainforest needed {_plural(retries, 'extra call', 'extra calls')} "
                 f"to return every listing.")

    job.step(label="Organising the results…", count=0)
    fresh = {p["asin"]: p for p in (normalize.normalize_product(r) for r in results)}

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
        "review_source": "amazon",
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
        "listing_retries": retries,
        "reviews": _review_log(walk),
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


def missing_reviews(slug: str, run_id: str) -> list[str]:
    """The live, fetched listings in a run that have no reviews at all."""
    run = storage.load_run(slug, run_id)
    return [p["asin"] for p in run.get("products", [])
            if p.get("fetch_ok") and not p.get("reviews")]


def fetch_missing_reviews(job, slug: str, run_id: str) -> dict:
    """
    Fill in reviews for the listings in an existing run that have none.

    Reads them from the site the run was extracted from, spends no Rainforest
    credits, and adds what it finds to extracted.json — it is machine output
    arriving late, not an edit. The one exception: a listing whose reviews were
    edited by hand keeps its list in edits.json, and that list hides anything
    underneath it, so the new reviews are added there instead.
    """
    started = time.perf_counter()
    directory = storage.run_dir(slug, run_id)
    extracted = storage.read_json(directory / "extracted.json")
    if not extracted:
        raise storage.StorageError("That run has no extracted data yet.")
    domain = extracted.get("amazon_domain") or settings.resolve("amazon_site")

    asins = missing_reviews(slug, run_id)
    n = len(asins)
    job.set_total(n)
    done = {"n": 0}

    def on_step() -> None:
        done["n"] += 1
        job.step(label=f"Reviews {done['n']}/{n}")

    job.step(label=f"Reviews 0/{n}", count=0)
    walk = _walk_reviews(job, directory / "raw", asins, domain, on_step)

    edits = storage.read_json(directory / "edits.json", {}) or {}
    edits_changed = False
    added = 0
    for product in extracted.get("products", []):
        fresh = walk["by_asin"].get(product.get("asin"))
        if not fresh:
            continue
        override = edits.get(product["asin"]) or {}
        target = override["reviews"] if isinstance(override.get("reviews"), list) \
            else product.setdefault("reviews", [])
        ids = {r.get("id") for r in target}
        bodies = {normalize.body_key(r.get("body")) for r in target}
        new = [r for r in fresh
               if r["id"] not in ids and normalize.body_key(r["body"]) not in bodies]
        target.extend(new)
        added += len(new)
        edits_changed = edits_changed or (target is override.get("reviews") and bool(new))

    extracted["review_source"] = "amazon"
    storage.write_json(directory / "extracted.json", extracted)
    if edits_changed:
        storage.write_json(directory / "edits.json", edits)

    summary = normalize.summarize(storage.load_run(slug, run_id).get("products", []))
    log = storage.read_json(directory / "run_log.json", {}) or {}
    # Notes about reviews that didn't arrive are stale now; drop them and say what happened.
    notes = [x for x in log.get("notes") or [] if not x.startswith(STALE_REVIEW_NOTES)]
    if walk["fetched"]:
        job.note(f"Reviews for {_plural(walk['fetched'], 'listing', 'listings')} were fetched "
                 f"from {domain} afterwards ({_plural(added, 'review', 'reviews')}).")
    log.update({
        "reviews": {**_review_log(walk), "added": added,
                    "elapsed_seconds": round(time.perf_counter() - started, 1),
                    "fetched_at": storage.now_iso()},
        "notes": notes + [x for x in job.notes if x not in notes],
        "failures": (log.get("failures") or []) + list(job.failures),
        **summary,
    })
    storage.write_json(directory / "run_log.json", log)

    return {"run_id": run_id, "slug": slug, **summary,
            "reviews_added": added, "listings_filled": walk["fetched"],
            "stopped": walk["stopped"]}
