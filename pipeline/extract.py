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

    if not to_fetch:
        job.step(label="Everything you pasted is already here…", count=0)

    results, walk = _fetch(job, raw_dir, to_fetch, domain, f"reviews-{run_id}")
    credits_left = None

    for result in results:
        if result.get("product"):
            credits_left = rainforest.credits_remaining(result["product"]) or credits_left
        result["amazon_reviews"] = walk["by_asin"].get(result["asin"]) or []

    api_calls = sum(r.get("attempts") or 1 for r in results)
    retries = api_calls - len(results)
    if retries > 0:
        job.note(f"Rainforest needed {_plural(retries, 'extra call', 'extra calls')} "
                 f"to return every listing.")

    return _finish_extraction(job, slug, run_id, items, domain, to_fetch, results, walk,
                              credits_left, api_calls, retries, started)


def _fetch(job, raw_dir, to_fetch: list[dict], domain: str, thread_name: str):
    """Listings and reviews for `to_fetch`, at the same time: listings in parallel
    from Rainforest, reviews one ASIN after another from Amazon. Raw payloads are
    written as they land. Returns (listing results, the review walk)."""
    n = len(to_fetch)
    job.set_total(2 * n)

    counts = {"listings": 0, "reviews": 0}
    lock = threading.Lock()

    def progress(which: str) -> None:
        with lock:
            counts[which] += 1
            label = f"Listings {counts['listings']}/{n} · Reviews {counts['reviews']}/{n}"
        job.step(label=label)

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

    reviewer = threading.Thread(target=review_worker, daemon=True, name=thread_name)
    if to_fetch:
        job.step(label=f"Listings 0/{n} · Reviews 0/{n}", count=0)
        reviewer.start()

    results = rainforest.fetch_all(to_fetch, domain, on_result=on_result)

    if reviewer.is_alive():
        job.step(label=f"Listings {n}/{n} · Reviews {counts['reviews']}/{n}", count=0)
    if to_fetch:
        reviewer.join()
    return results, walk


def _finish_extraction(job, slug, run_id, items, domain, to_fetch, results, walk,
                       credits_left, api_calls, retries, started) -> dict:
    """Normalize what was fetched, fold in what the group held, write the run."""
    directory = storage.run_dir(slug, run_id)
    raw_dir = directory / "raw"
    carried = len(items) - len(to_fetch)

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


def scraped(slug: str, run_id: str, asin: str) -> bool:
    """Whether this listing's reviews have ever been read from Amazon in this run."""
    return (storage.run_dir(slug, run_id) / "raw" / f"{asin}.amazon_reviews.json").is_file()


def unscraped(slug: str, run_id: str) -> list[str]:
    """The live, fetched listings whose reviews were never read from Amazon: runs
    from before November 2026, and listings a stopped fetch didn't reach. These
    are the ones with no customer photos to show."""
    run = storage.load_run(slug, run_id)
    return [p["asin"] for p in run.get("products", [])
            if p.get("fetch_ok") and not scraped(slug, run_id, p["asin"])]


SCOPES = {"missing": missing_reviews, "unscraped": unscraped}


def fetch_missing_reviews(job, slug: str, run_id: str, scope: str = "missing") -> dict:
    """
    Read reviews from Amazon for listings in an existing run.

    `scope` picks the listings: "missing" is those with no reviews at all,
    "unscraped" those never read from Amazon, whatever reviews they hold from
    Rainforest or from pasting. A review the listing already has — the same id,
    or the same text — is left as it is and simply gains its photos, since those
    are looked up from the scrape. Only reviews it doesn't have are added.

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

    asins = SCOPES[scope](slug, run_id)
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
    added = matched = 0
    for product in extracted.get("products", []):
        fresh = walk["by_asin"].get(product.get("asin"))
        if not fresh:
            continue
        override = edits.get(product["asin"]) or {}
        target = override["reviews"] if isinstance(override.get("reviews"), list) \
            else product.setdefault("reviews", [])
        ids = {r.get("id") for r in target}
        # Keyed by text as well, so a review pasted by hand (its own id, and often
        # cut off at "Read more") is recognised and not added a second time.
        bodies = {normalize.body_key(r.get("body")): r.get("id") for r in target}
        new = [r for r in fresh
               if r["id"] not in ids
               and not normalize.find_duplicate(normalize.body_key(r["body"]), bodies)[1]]
        matched += len(fresh) - len(new)
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
                 f"from {domain} afterwards: {_plural(added, 'new review', 'new reviews')} added"
                 + (f"; {matched} were already here and now carry any photos they have." if matched else "."))
    log.update({
        "reviews": {**_review_log(walk), "added": added, "matched": matched, "scope": scope,
                    "elapsed_seconds": round(time.perf_counter() - started, 1),
                    "fetched_at": storage.now_iso()},
        "notes": notes + [x for x in job.notes if x not in notes],
        "failures": (log.get("failures") or []) + list(job.failures),
        **summary,
    })
    storage.write_json(directory / "run_log.json", log)

    return {"run_id": run_id, "slug": slug, **summary,
            "reviews_added": added, "reviews_matched": matched,
            "listings_filled": walk["fetched"], "stopped": walk["stopped"]}


# ---------------------------------------------------------------------------
# Adding listings to a run that already exists
# ---------------------------------------------------------------------------

def split_new(slug: str, run_id: str, items: list[dict]) -> dict:
    """Which pasted ASINs are new to this run, already in it, or removed from it."""
    run = storage.load_run(slug, run_id, include_deleted=True)
    here = {p["asin"]: bool(p.get("deleted")) for p in run.get("products", [])}
    return {
        "new": [i for i in items if i["asin"] not in here],
        "in_run": [i["asin"] for i in items if here.get(i["asin"]) is False],
        "removed": [i["asin"] for i in items if here.get(i["asin"]) is True],
    }


def add_to_run(job, slug: str, run_id: str, items: list[dict],
               to_fetch: list[dict]) -> dict:
    """
    Add listings to an existing run: `items` are the new ASINs, `to_fetch` the
    ones the group doesn't already hold complete.

    The same steps as a new run — listings from Rainforest in parallel, reviews
    from Amazon one ASIN at a time, then the group's own copies folded in — but
    appended to this run's files instead of starting new ones. Nothing already in
    the run is touched, and the run's existing parse stays as it was: the new
    listings are simply not in its results until it is parsed again.
    """
    started = time.perf_counter()
    directory = storage.run_dir(slug, run_id)
    raw_dir = directory / "raw"
    extracted = storage.read_json(directory / "extracted.json")
    if not extracted:
        raise storage.StorageError("That run has no extracted data yet.")
    domain = extracted.get("amazon_domain") or settings.resolve("amazon_site")

    present = {p.get("asin") for p in extracted.get("products", [])}
    items = [i for i in items if i["asin"] not in present]
    wanted = {i["asin"] for i in items}
    to_fetch = [i for i in to_fetch if i["asin"] in wanted]

    results, walk = _fetch(job, raw_dir, to_fetch, domain, f"add-{run_id}")
    credits_left = None
    for result in results:
        if result.get("product"):
            credits_left = rainforest.credits_remaining(result["product"]) or credits_left
        result["amazon_reviews"] = walk["by_asin"].get(result["asin"]) or []

    job.step(label="Organising the results…", count=0)
    fresh = {p["asin"]: p for p in (normalize.normalize_product(r) for r in results)}
    resolved = compact.resolve(slug, items, fresh, run_id)
    compact.copy_raw(slug, resolved["raw_from"], raw_dir)

    extracted.setdefault("products", []).extend(resolved["products"])
    storage.write_json(directory / "extracted.json", extracted)
    if resolved["edits"]:
        edits = storage.read_json(directory / "edits.json", {}) or {}
        edits.update(resolved["edits"])
        storage.write_json(directory / "edits.json", edits)

    added = [p["asin"] for p in resolved["products"]]
    carried = len(items) - len(to_fetch)
    if carried:
        job.note(f"{_plural(carried, 'listing was', 'listings were')} already complete in this "
                 f"group and carried across instead of fetched again.")
    job.note(f"{_plural(len(added), 'listing', 'listings')} added to this run afterwards.")

    summary = normalize.summarize(storage.load_run(slug, run_id).get("products", []))
    log = storage.read_json(directory / "run_log.json", {}) or {}
    api_calls = sum(r.get("attempts") or 1 for r in results)
    log.update({
        "credits_remaining": credits_left if credits_left is not None else log.get("credits_remaining"),
        "api_calls": (log.get("api_calls") or 0) + api_calls,
        "fetched": (log.get("fetched") or 0) + len(to_fetch),
        "carried": (log.get("carried") or 0) + carried,
        "outcomes": (log.get("outcomes") or []) + resolved["outcomes"],
        "inputs": (log.get("inputs") or []) + items,
        "notes": (log.get("notes") or []) + [x for x in job.notes if x not in (log.get("notes") or [])],
        "failures": (log.get("failures") or []) + list(job.failures),
        "additions": (log.get("additions") or []) + [{
            "at": storage.now_iso(), "asins": added, "api_calls": api_calls,
            "reviews": _review_log(walk),
            "elapsed_seconds": round(time.perf_counter() - started, 1),
        }],
        **summary,
    })
    storage.write_json(directory / "run_log.json", log)

    return {"run_id": run_id, "slug": slug, **summary, "added": added,
            "credits_remaining": credits_left, "stopped": walk["stopped"]}
