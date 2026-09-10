"""
Competitor Feature-Benefit Extractor — local Flask server.

Run:  python app.py
Then: http://127.0.0.1:5000  (opened automatically)
"""

from __future__ import annotations

import sys
import threading
import webbrowser

from flask import Flask, jsonify, request, send_from_directory

import config
from pipeline import asins, compact, extract, jobs, normalize, storage

app = Flask(__name__, static_folder="static", static_url_path="/static")


# ---------------------------------------------------------------------------
# Errors — every failure reaches the UI as a plain sentence, never a traceback
# ---------------------------------------------------------------------------

@app.errorhandler(storage.StorageError)
def handle_storage_error(exc: storage.StorageError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(404)
def handle_404(_exc):
    return jsonify({"error": "That page or record doesn't exist."}), 404


@app.errorhandler(Exception)
def handle_unexpected(exc: Exception):
    app.logger.exception("Unhandled error")
    return jsonify({
        "error": f"Something went wrong on the server: {exc}",
        "kind": exc.__class__.__name__,
    }), 500


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """What the app can and cannot do right now, so the UI can say so plainly."""
    return jsonify({
        "ok": True,
        "amazon_domain": config.AMAZON_DOMAIN,
        "review_target": config.REVIEW_TARGET,
        "tag_model": config.OPENAI_TAG_MODEL,
        "group_model": config.OPENAI_GROUP_MODEL,
        "rainforest_key_set": bool(config.RAINFOREST_API_KEY),
        "openai_key_set": bool(config.OPENAI_API_KEY),
        "data_dir": str(config.DATA_DIR),
    })


@app.get("/api/groups")
def api_list_groups():
    return jsonify({"groups": storage.list_groups()})


@app.post("/api/groups")
def api_create_group():
    payload = request.get_json(silent=True) or {}
    group = storage.create_group(payload.get("name", ""))
    return jsonify({"group": group}), 201


@app.get("/api/groups/<slug>")
def api_get_group(slug: str):
    return jsonify({"group": storage.get_group(slug)})


@app.delete("/api/groups/<slug>")
def api_delete_group(slug: str):
    """Move a whole group to data/_trash/ — recoverable in the file browser."""
    return jsonify({"trashed": storage.trash_group(slug).name})


@app.post("/api/parse-asins")
def api_parse_asins():
    """Preview what a pasted block resolves to. Pure regex — no network calls."""
    payload = request.get_json(silent=True) or {}
    return jsonify(asins.parse_input_block(payload.get("text", "")))


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

@app.post("/api/groups/<slug>/extract")
def api_extract(slug: str):
    """Kick off a Rainforest run in the background and hand back a job id."""
    payload = request.get_json(silent=True) or {}
    parsed = asins.parse_input_block(payload.get("text", ""))
    items = parsed["items"]

    if not items:
        raise storage.StorageError(
            "No Amazon ASINs found in what you pasted. Each line should be a "
            "product URL (one containing /dp/) or a bare 10-character ASIN."
        )
    if not config.RAINFOREST_API_KEY:
        raise storage.StorageError("RAINFOREST_API_KEY is empty in config.py.")

    storage.get_group(slug)  # raises if the group is gone
    domain = payload.get("domain") or config.AMAZON_DOMAIN
    run_id = storage.create_run(slug, items)

    jobs.prune()
    job = jobs.start(
        f"Fetching {len(items)} listings…",
        lambda j: extract.run_extraction(j, slug, run_id, items, domain),
        total=len(items),
    )
    return jsonify({"job_id": job.id, "run_id": run_id, "asin_count": len(items)}), 202


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise storage.StorageError("That job is no longer around — reload the page.")
    return jsonify(job.snapshot())


@app.get("/api/runs/<slug>/<run_id>")
def api_run(slug: str, run_id: str):
    """A run's products (with edits applied), its log, and live stats.

    `log` is the record of what the fetch did and never changes. `stats` is
    recomputed over the post-edit products, so the summary strip describes what
    is actually on screen.
    """
    directory = storage.run_dir(slug, run_id)
    group = storage.read_json(storage.group_file(slug), {}) or {}

    # Removed ASINs come along carrying `deleted: true`, so the run view can
    # offer to restore them. Stats count only what is still live.
    run = storage.load_run(slug, run_id, include_deleted=True)
    live = [p for p in run["products"] if not p.get("deleted")]

    return jsonify({
        "group": {"name": group.get("name", slug), "slug": slug},
        "run": run,
        "stats": normalize.summarize(live),
        "log": storage.read_json(directory / "run_log.json", {}) or {},
        "meta": storage.read_run_meta(slug, run_id),
    })


@app.delete("/api/runs/<slug>/<run_id>")
def api_delete_run(slug: str, run_id: str):
    """Move one run to data/<slug>/_trash/ — recoverable in the file browser."""
    return jsonify({"trashed": storage.trash_run(slug, run_id).name})


@app.put("/api/runs/<slug>/<run_id>/meta")
def api_set_run_label(slug: str, run_id: str):
    """Name a run. The run id stays the timestamp it has always been."""
    payload = request.get_json(silent=True) or {}
    return jsonify({"meta": storage.set_run_label(slug, run_id, payload.get("label", ""))})


# ---------------------------------------------------------------------------
# Editing — edits.json is an overlay, extracted.json is never touched
# ---------------------------------------------------------------------------

@app.put("/api/runs/<slug>/<run_id>/edits")
def api_save_edits(slug: str, run_id: str):
    """Merge `{ASIN: {field: value}}` into the overlay. null removes a field."""
    payload = request.get_json(silent=True) or {}
    patch = payload.get("patch") or {}
    if not isinstance(patch, dict) or not patch:
        raise storage.StorageError("Nothing to save.")

    clean = {asin: _clean_fields(slug, run_id, asin, fields)
             for asin, fields in patch.items()}
    storage.merge_edits(slug, run_id, clean)
    return jsonify(_edit_result(slug, run_id, list(patch)))


def _clean_fields(slug: str, run_id: str, asin: str, fields) -> dict:
    """Normalize an edited field the same way the extractor normalizes a fetch.

    Hand-typed text goes through the same emoji strip and whitespace collapse as
    Rainforest's, and a typed price is parsed back into {raw, value, currency},
    so nothing downstream has to care which of the two it is looking at.
    """
    if not isinstance(fields, dict):
        raise storage.StorageError(f"Malformed edit for {asin}.")

    product = _find_product(slug, run_id, asin)
    clean = {}

    for key, value in fields.items():
        if value is None or key == storage.DELETED_KEY:
            clean[key] = value

        elif key == "title":
            clean[key] = normalize.clean_text(value) or None

        elif key == "price":
            raw = value.get("raw") if isinstance(value, dict) else value
            clean[key] = normalize.parse_price(raw, product.get("price"))

        elif key == "rating":
            try:
                clean[key] = round(float(value), 1)
            except (TypeError, ValueError):
                clean[key] = None

        elif key == "feature_bullets":
            if not isinstance(value, list):
                raise storage.StorageError("Bullets have to be a list.")
            clean[key] = [t for t in (normalize.clean_text(b) for b in value) if t]

        elif key == "reviews":
            if not isinstance(value, list):
                raise storage.StorageError("Reviews have to be a list.")
            clean[key] = [_clean_review(r) for r in value if isinstance(r, dict)]

        else:
            raise storage.StorageError(f"{key} isn't an editable field.")

    return clean


def _clean_review(review: dict) -> dict:
    review = dict(review)
    review["title"] = normalize.clean_text(review.get("title"))
    review["body"] = "\n".join(
        normalize.clean_text(line) for line in str(review.get("body") or "").splitlines()
    ).strip()
    try:
        review["rating"] = float(review["rating"]) if review.get("rating") is not None else None
    except (TypeError, ValueError):
        review["rating"] = None
    return review


@app.delete("/api/runs/<slug>/<run_id>/edits/<asin>")
def api_restore_asin(slug: str, run_id: str, asin: str):
    """Drop every override for one ASIN, back to what Rainforest returned."""
    storage.clear_edits(slug, run_id, asin)
    return jsonify(_edit_result(slug, run_id, [asin]))


@app.post("/api/runs/<slug>/<run_id>/reviews")
def api_add_reviews(slug: str, run_id: str):
    """Append pasted reviews to an ASIN — one blank line between each.

    Parsed here rather than in the browser so the emoji strip and the minimum
    body length stay in one place, next to the rules the fetched reviews follow.
    """
    payload = request.get_json(silent=True) or {}
    asin = payload.get("asin")

    product = _find_product(slug, run_id, asin)
    existing = list(product.get("reviews") or [])
    added = normalize.parse_pasted_reviews(payload.get("text", ""), existing)
    if not added:
        raise storage.StorageError(
            "Nothing there looked like a review. Paste the review text with a "
            "blank line between each one."
        )

    storage.merge_edits(slug, run_id, {asin: {"reviews": existing + added}})
    return jsonify({"added": len(added), **_edit_result(slug, run_id, [asin])})


def _find_product(slug: str, run_id: str, asin: str) -> dict:
    run = storage.load_run(slug, run_id, include_deleted=True)
    for product in run.get("products", []):
        if product.get("asin") == asin:
            return product
    raise storage.StorageError(f"{asin} isn't in this run.")


def _edit_result(slug: str, run_id: str, asins: list[str]) -> dict:
    """What every edit hands back: fresh stats, and the records that changed.

    Returning the records means the browser never has to reconstruct what an
    override or a restore did — it just swaps in what the server now reads.
    """
    products = storage.load_run(slug, run_id, include_deleted=True).get("products", [])
    live = [p for p in products if not p.get("deleted")]
    return {
        "ok": True,
        "stats": normalize.summarize(live),
        "products": [p for p in products if p.get("asin") in set(asins)],
    }


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

@app.post("/api/groups/<slug>/compact")
def api_compact(slug: str):
    """Merge a group's runs into one. `?dry_run=1` previews without writing."""
    if request.args.get("dry_run"):
        return jsonify({"preview": compact.public_report(compact.plan_compaction(slug))})
    return jsonify({"result": compact.compact(slug)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"\n  Competitor Feature-Benefit Extractor")
    print(f"  Serving   {url}")
    print(f"  Data dir  {config.DATA_DIR}")
    if not config.RAINFOREST_API_KEY:
        print("  ! RAINFOREST_API_KEY is empty in config.py — extraction will fail.")
    if not config.OPENAI_API_KEY:
        print("  ! OPENAI_API_KEY is empty in config.py — tagging will fail (needed from Phase 3).")
    print()

    if config.OPEN_BROWSER and "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
