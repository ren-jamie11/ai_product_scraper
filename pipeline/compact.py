"""
Compaction: fold a group's runs into one clean run.

A group accumulates a folder per extraction attempt — Rainforest returns
reviews only intermittently, so decent coverage means running the same ASINs
more than once. Compaction takes the best copy of every ASIN worth keeping and
writes it as a single new run, moving the sources to _trash/.

Which copy wins:

  1. A copy with both bullets and reviews beats a copy with only one of them.
  2. Otherwise the most recent copy wins.

Reviews are then unioned across every copy of that ASIN rather than taken from
the winner alone. Reviews are the scarce resource here — an older run may hold
ones you pasted by hand — and merging them back costs nothing.

The pristine/overlay split survives: the compacted run's extracted.json holds
each winner's untouched Rainforest record, and its edits.json holds that ASIN's
overrides, so you can still see what the API actually returned.
"""

from __future__ import annotations

import shutil

import config
from pipeline import normalize, storage

# Internal working state on a kept entry, dropped before the report is sent to
# the browser — it holds whole product records.
_INTERNAL = ("winner", "others", "review_union")


def _has(product: dict, field: str) -> bool:
    return bool(product.get(field))


def _rank(candidate: dict) -> tuple[int, str]:
    """Sort key for picking the winning copy of an ASIN.

    Run ids are timestamps that sort correctly as strings, so "has both, then
    most recent" is exactly this tuple under max().
    """
    merged = candidate["merged"]
    both = _has(merged, "feature_bullets") and _has(merged, "reviews")
    return (1 if both else 0, candidate["run_id"])


def _strip(record: dict) -> dict:
    """Drop the display-only keys load_run adds, so bases stay pristine."""
    return {k: v for k, v in record.items() if k not in ("edited_fields", "deleted")}


def _collect(slug: str, run_ids: list[str]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Every eligible copy of every ASIN, newest run first.

    Insertion order matters: dicts keep it, so the compacted run lists the
    newest run's ASINs in their original order, with older-only ASINs appended.
    """
    candidates: dict[str, list[dict]] = {}
    dropped: list[dict] = []

    for run_id in run_ids:
        try:
            run = storage.load_run(slug, run_id)
        except storage.StorageError:
            # A run folder with no extracted.json — an extraction that died
            # before writing. Nothing to salvage, nothing worth reporting.
            continue

        extracted = storage.read_json(
            storage.run_dir(slug, run_id) / "extracted.json", {}) or {}
        base_records = {p.get("asin"): p for p in extracted.get("products", [])}
        edits = storage.read_edits(slug, run_id)

        for merged in run.get("products", []):
            asin = merged.get("asin")
            if not asin:
                continue

            if not merged.get("fetch_ok"):
                dropped.append({"asin": asin, "run_id": run_id, "reason": "the fetch failed"})
                continue
            if not (_has(merged, "feature_bullets") or _has(merged, "reviews")):
                dropped.append({"asin": asin, "run_id": run_id,
                                "reason": "no bullets and no reviews"})
                continue

            override = dict(edits.get(asin) or {})
            override.pop(storage.DELETED_KEY, None)
            candidates.setdefault(asin, []).append({
                "asin": asin,
                "run_id": run_id,
                "merged": merged,
                "base": base_records.get(asin),
                "override": override,
            })

    return candidates, dropped


def _union_reviews(copies: list[dict]) -> list[dict]:
    """Winner's reviews first, then whatever the other copies add.

    Fetched reviews dedupe on their Amazon id. Manual ids can't: `manual_1`
    means a different review in every run, so pasted reviews dedupe on their
    text and are re-keyed to keep ids unique within the product.
    """
    reviews: list[dict] = []
    seen_ids: set[str] = set()
    seen_bodies: set[str] = set()

    for candidate in copies:
        for review in candidate["merged"].get("reviews") or []:
            review = dict(review)
            body = (review.get("body") or "").strip()

            if review.get("source") == "manual" or not review.get("id"):
                if body in seen_bodies:
                    continue
                counter = 1
                while f"manual_{counter}" in seen_ids:
                    counter += 1
                review["id"] = f"manual_{counter}"
            elif review["id"] in seen_ids:
                continue

            seen_ids.add(review["id"])
            seen_bodies.add(body)
            reviews.append(review)

    return reviews


def plan_compaction(slug: str) -> dict:
    """Work out what compaction would do. Pure — touches nothing on disk."""
    storage.get_group(slug)  # raises if the group is gone

    runs = storage.list_runs(slug)
    if not runs:
        raise storage.StorageError("This group has no runs to compact yet.")

    run_ids = [r["run_id"] for r in runs]
    candidates, dropped = _collect(slug, run_ids)

    kept = []
    for asin, copies in candidates.items():
        winner = max(copies, key=_rank)
        others = [c for c in copies if c is not winner]
        reviews = _union_reviews([winner] + others)

        kept.append({
            "asin": asin,
            "title": winner["merged"].get("title"),
            "from_run": winner["run_id"],
            "bullets": len(winner["merged"].get("feature_bullets") or []),
            "reviews": len(reviews),
            "reviews_gained": len(reviews) - len(winner["merged"].get("reviews") or []),
            "merged_from": len(copies),
            "winner": winner,
            "others": others,
            "review_union": reviews,
        })

    # An ASIN only counts as dropped if no run had a usable copy of it.
    dropped = [d for d in dropped if d["asin"] not in candidates]

    return {
        "slug": slug,
        "run_ids": run_ids,
        "runs": len(run_ids),
        "kept": kept,
        "kept_count": len(kept),
        "dropped": dropped,
        "dropped_asins": sorted({d["asin"] for d in dropped}),
        "duplicates": sum(1 for k in kept if k["merged_from"] > 1),
        "reviews_total": sum(k["reviews"] for k in kept),
        "reviews_gained": sum(k["reviews_gained"] for k in kept),
        "parses": sorted({p for r in runs for p in (r.get("parses") or [])}),
    }


def public_report(report: dict) -> dict:
    """The same report without the whole product records — safe to send."""
    return {
        **{k: v for k, v in report.items() if k != "kept"},
        "kept": [{k: v for k, v in entry.items() if k not in _INTERNAL}
                 for entry in report["kept"]],
    }


def _notes(slug: str, report: dict) -> list[str]:
    notes = [
        f"Compacted {report['runs']} {'run' if report['runs'] == 1 else 'runs'} into "
        f"this one, keeping the best copy of each ASIN. The originals are in "
        f"data/{slug}/_trash/."
    ]
    gained = report["reviews_gained"]
    if gained:
        notes.append(
            f"Merged in {gained} extra {'review' if gained == 1 else 'reviews'} that "
            f"only the older runs had."
        )
    if report["dropped_asins"]:
        notes.append(
            f"Left out {', '.join(report['dropped_asins'])} — no bullets and no "
            f"reviews in any run."
        )
    if report["parses"]:
        notes.append(
            "Parse results were not carried over — they refer to the old runs, "
            "which are in _trash/."
        )
    return notes


def compact(slug: str) -> dict:
    """Write the compacted run, then move every source run to _trash/."""
    report = plan_compaction(slug)
    if not report["kept"]:
        raise storage.StorageError(
            "Nothing to compact — no ASIN in this group has bullets or reviews."
        )

    run_id, directory = storage.claim_run_dir(slug)
    products, edits = [], {}

    for entry in report["kept"]:
        winner = entry["winner"]
        # extracted.json is the pristine record. Falling back to the merged
        # view only matters if extracted.json lost it, and then the overlay is
        # redundant rather than wrong.
        base = _strip(winner["base"] or winner["merged"])
        products.append(base)

        override = dict(winner["override"])
        if entry["review_union"] != (base.get("reviews") or []):
            override["reviews"] = entry["review_union"]
        if override:
            edits[entry["asin"]] = override

        raw_src = storage.run_dir(slug, winner["run_id"]) / "raw"
        for name in (f"{entry['asin']}.product.json", f"{entry['asin']}.reviews.json"):
            if (raw_src / name).exists():
                shutil.copy2(raw_src / name, directory / "raw" / name)

    storage.write_json(directory / "extracted.json", {
        "group": slug,
        "run_id": run_id,
        "amazon_domain": _domain(slug, report["run_ids"]),
        "fetched_at": storage.now_iso(),
        "compacted_from": report["run_ids"],
        "products": products,
    })
    storage.write_json(directory / "edits.json", edits)

    notes = _notes(slug, report)
    storage.write_json(directory / "run_log.json", {
        "run_id": run_id,
        "started_at": storage.now_iso(),
        "state": "done",
        "compacted_from": report["run_ids"],
        "compacted_at": storage.now_iso(),
        "dropped": report["dropped"],
        "notes": notes,
        "failures": [],
        **normalize.summarize(storage.load_run(slug, run_id).get("products", [])),
    })

    # Last, so any failure above leaves every source run exactly where it was.
    trashed = [storage.trash_run(slug, source).name for source in report["run_ids"]]

    return {
        "run_id": run_id,
        "slug": slug,
        "kept_count": report["kept_count"],
        "dropped_asins": report["dropped_asins"],
        "duplicates": report["duplicates"],
        "reviews_total": report["reviews_total"],
        "reviews_gained": report["reviews_gained"],
        "trashed": trashed,
        "notes": notes,
    }


def _domain(slug: str, run_ids: list[str]) -> str:
    for run_id in run_ids:
        extracted = storage.read_json(
            storage.run_dir(slug, run_id) / "extracted.json", {}) or {}
        if extracted.get("amazon_domain"):
            return extracted["amazon_domain"]
    return config.AMAZON_DOMAIN
