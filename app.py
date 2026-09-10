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
from pipeline import asins, extract, jobs, storage

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
    """A run's products (with edits applied) plus its log."""
    directory = storage.run_dir(slug, run_id)
    group = storage.read_json(storage.group_file(slug), {}) or {}
    return jsonify({
        "group": {"name": group.get("name", slug), "slug": slug},
        "run": storage.load_run(slug, run_id),
        "log": storage.read_json(directory / "run_log.json", {}) or {},
    })


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
