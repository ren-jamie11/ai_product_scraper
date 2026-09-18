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
from pipeline import (asins, compact, extract, grouping, jobs, normalize, results,
                      settings, storage, tagging, themes)

app = Flask(__name__, static_folder="static", static_url_path="/static")


# ---------------------------------------------------------------------------
# Errors — every failure reaches the UI as a plain sentence, never a traceback
# ---------------------------------------------------------------------------

@app.errorhandler(storage.StorageError)
def handle_storage_error(exc: storage.StorageError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(tagging.TaggingError)
def handle_tagging_error(exc: tagging.TaggingError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(grouping.GroupingError)
def handle_grouping_error(exc: grouping.GroupingError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(themes.ThemeError)
def handle_theme_error(exc: themes.ThemeError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(settings.SettingsError)
def handle_settings_error(exc: settings.SettingsError):
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
    """Preview what a pasted block resolves to.

    No network calls: the ASINs come from a regex and the "already have" answer
    comes off disk, so the cost of an extraction is visible before committing
    to it.
    """
    payload = request.get_json(silent=True) or {}
    parsed = asins.parse_input_block(payload.get("text", ""))

    slug = payload.get("slug")
    if slug and storage.group_file(slug).exists():
        parsed.update(_split_work(slug, parsed["items"], payload.get("refetch")))
    return jsonify(parsed)


def _split_work(slug: str, items: list[dict], refetch) -> dict:
    """Which pasted ASINs need buying, and which the group already holds.

    With `refetch` set, everything is fetched — prices and ratings go stale,
    and this is the way to refresh them without deleting good data first.
    """
    if refetch:
        return {"held": [], "to_fetch": [i["asin"] for i in items], "refetch": True}

    found = compact.survey(slug, [i["asin"] for i in items])
    return {"held": found["held"], "to_fetch": found["to_fetch"], "refetch": False}


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

    storage.get_group(slug)  # raises if the group is gone

    # Decided here, not in the browser: the preview can go stale if another run
    # finished in between, and credits are only ever spent on this path.
    work = _split_work(slug, items, payload.get("refetch"))
    needed = {a for a in work["to_fetch"]}
    to_fetch = [i for i in items if i["asin"] in needed]

    if to_fetch and not config.RAINFOREST_API_KEY:
        raise storage.StorageError("RAINFOREST_API_KEY is empty in config.py.")

    domain = payload.get("domain") or config.AMAZON_DOMAIN
    run_id = storage.create_run(slug, items)

    jobs.prune()
    job = jobs.start(
        f"Fetching {len(to_fetch)} listings…" if to_fetch
        else "Collecting listings you already have…",
        lambda j: extract.run_extraction(j, slug, run_id, items, domain, to_fetch),
        total=len(to_fetch),
    )
    return jsonify({
        "job_id": job.id,
        "run_id": run_id,
        "asin_count": len(items),
        "fetch_count": len(to_fetch),
        "held_count": len(items) - len(to_fetch),
    }), 202


# ---------------------------------------------------------------------------
# Step 2 — tagging
#
# A parse never touches the run it reads. Each one writes its own parse-<ts>/
# folder, so re-parsing after a prompt change leaves the earlier output intact
# and the two can be compared side by side.
# ---------------------------------------------------------------------------

@app.get("/api/runs/<slug>/<run_id>/parse-estimate")
def api_parse_estimate(slug: str, run_id: str):
    """Body count and cost, for the confirm dialog. Spends nothing."""
    products = storage.load_run(slug, run_id).get("products", [])
    return jsonify({
        "estimate": tagging.estimate(normalize.build_bodies(products)),
        "openai_key_set": bool(config.OPENAI_API_KEY),
        "min_tag_chars": config.MIN_TAG_CHARS,
    })


@app.post("/api/runs/<slug>/<run_id>/parse")
def api_parse(slug: str, run_id: str):
    """Kick off tagging in the background and hand back a job id."""
    if not config.OPENAI_API_KEY:
        raise storage.StorageError("OPENAI_API_KEY is empty in config.py.")

    products = storage.load_run(slug, run_id).get("products", [])
    bodies = normalize.build_bodies(products)
    if not bodies:
        raise storage.StorageError(
            "There is nothing to tag in this run — no listing has bullets or "
            "reviews. Add some by hand above, then parse again."
        )

    jobs.prune()
    to_tag = len(tagging.partition(bodies)[0])
    job = jobs.start(
        f"Tagging {to_tag} bodies…",
        lambda j: tagging.run_tagging(j, slug, run_id),
        total=to_tag,
    )
    return jsonify({"job_id": job.id, "run_id": run_id, "to_tag": to_tag}), 202


@app.get("/api/runs/<slug>/<run_id>/parses")
def api_list_parses(slug: str, run_id: str):
    return jsonify({"parses": storage.list_parses(slug, run_id)})


@app.get("/api/runs/<slug>/<run_id>/parses/<parse_id>")
def api_parse_result(slug: str, run_id: str, parse_id: str):
    """One parse's tags, clusters and logs. Phase 5 renders these.

    `tagged` and `clusters` come back together on purpose: a cluster holds uids,
    and resolving those to the review text a tag came from needs both halves of
    the join in one response.
    """
    directory = storage.run_dir(slug, run_id) / parse_id
    if storage.is_hidden(parse_id) or not directory.is_dir():
        raise storage.StorageError("That parse no longer exists.")

    tagged = storage.read_json(directory / "tagged.json")
    if not tagged:
        raise storage.StorageError("That parse didn't finish — nothing was written.")

    return jsonify({
        "tagged": tagged,
        "log": storage.read_json(directory / "parse_log.json", {}) or {},
        "clusters": storage.read_json(directory / "clusters.json"),
        "cluster_log": storage.read_json(directory / "cluster_log.json", {}) or {},
    })


# ---------------------------------------------------------------------------
# Step 4 — results
# ---------------------------------------------------------------------------

@app.get("/api/runs/<slug>/<run_id>/parses/<parse_id>/results")
def api_results(slug: str, run_id: str, parse_id: str):
    """Clusters with their tags split by source, every tag attributed to a sentence,
    and the bodies and products they point at. Only a grouped parse has results;
    an ungrouped one gets a plain 400 the UI shows as an empty state."""
    return jsonify(results.build(slug, run_id, parse_id))


# ---------------------------------------------------------------------------
# Step 3 — grouping
# ---------------------------------------------------------------------------

def _lists_arg(value) -> list[str] | None:
    """The optional `lists` narrowing on the grouping routes: a JSON array on the
    POST, a comma-separated query string on the estimate. None means every list."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise grouping.GroupingError("`lists` has to be a list of tag-list names.")
    return list(grouping.which_lists(value))


@app.get("/api/runs/<slug>/<run_id>/parses/<parse_id>/group-estimate")
def api_group_estimate(slug: str, run_id: str, parse_id: str):
    """Tag counts per list and what grouping will cost. Spends nothing.

    `?lists=features` narrows it to the lists a partial re-run would group."""
    only = _lists_arg(request.args.get("lists"))
    tagged = grouping.load_tagged(slug, run_id, parse_id)
    return jsonify({
        "estimate": grouping.estimate(tagged, only=only),
        "openai_key_set": bool(config.OPENAI_API_KEY),
        "already_grouped": bool(
            storage.read_json(storage.run_dir(slug, run_id) / parse_id / "clusters.json")
        ),
        "auto_themes": bool(settings.resolve("auto_themes")),
        "theme_min_clusters": config.THEME_MIN_CLUSTERS,
    })


@app.post("/api/runs/<slug>/<run_id>/parses/<parse_id>/group")
def api_group(slug: str, run_id: str, parse_id: str):
    """Kick off clustering in the background and hand back a job id.

    A JSON body of `{"lists": ["features"]}` re-groups only those lists and keeps
    the rest of clusters.json, and the themes built on it, exactly as they are."""
    if not config.OPENAI_API_KEY:
        raise storage.StorageError("OPENAI_API_KEY is empty in config.py.")

    payload = request.get_json(silent=True) or {}
    only = _lists_arg(payload.get("lists"))

    tagged = grouping.load_tagged(slug, run_id, parse_id)
    to_group = grouping.estimate(tagged, only=only)["to_group"]
    if not to_group:
        raise grouping.GroupingError(
            "This parse produced no features, complaints or care instructions, so "
            "there is nothing to group."
        )

    jobs.prune()
    verb = "Re-grouping" if only else "Grouping"
    job = jobs.start(
        f"{verb} {to_group} tag {'list' if to_group == 1 else 'lists'}…",
        lambda j: grouping.run_grouping(j, slug, run_id, parse_id, only=only),
        total=to_group,
    )
    return jsonify({"job_id": job.id, "parse_id": parse_id, "to_group": to_group,
                    "lists": only}), 202


# ---------------------------------------------------------------------------
# Step 3b — themes
#
# Normally run inside the grouping job (the "auto themes" setting). These two
# routes are the manual path: the "Group into themes" button on the results view.
# ---------------------------------------------------------------------------

@app.get("/api/runs/<slug>/<run_id>/parses/<parse_id>/theme-estimate")
def api_theme_estimate(slug: str, run_id: str, parse_id: str):
    """Which lists qualify and what theming will cost. Spends nothing."""
    clusters = themes.load_clusters(slug, run_id, parse_id)
    directory = storage.run_dir(slug, run_id) / parse_id
    return jsonify({
        "estimate": themes.estimate(clusters),
        "openai_key_set": bool(config.OPENAI_API_KEY),
        "already_themed": themes.read_themes(directory, clusters) is not None,
    })


@app.post("/api/runs/<slug>/<run_id>/parses/<parse_id>/theme")
def api_theme(slug: str, run_id: str, parse_id: str):
    """Kick off theming in the background and hand back a job id."""
    if not config.OPENAI_API_KEY:
        raise storage.StorageError("OPENAI_API_KEY is empty in config.py.")

    clusters = themes.load_clusters(slug, run_id, parse_id)
    to_theme = themes.estimate(clusters)["to_theme"]
    if not to_theme:
        raise themes.ThemeError(
            f"No list has {config.THEME_MIN_CLUSTERS} or more clusters, so there is "
            f"nothing to group into themes."
        )

    jobs.prune()
    job = jobs.start(
        f"Grouping {to_theme} {'list' if to_theme == 1 else 'lists'} into themes…",
        lambda j: themes.run_themes(j, slug, run_id, parse_id),
        total=to_theme,
    )
    return jsonify({"job_id": job.id, "parse_id": parse_id, "to_theme": to_theme}), 202


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def api_get_settings():
    return jsonify({"settings": settings.effective(), "options": settings.options()})


@app.put("/api/settings")
def api_put_settings():
    patch = request.get_json(silent=True) or {}
    return jsonify({
        "settings": settings.write(patch),
        "options": settings.options(),
    })


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
        "parses": storage.list_parses(slug, run_id),
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
    """Append pasted reviews to an ASIN — copied from Amazon, or blank-line separated.

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
            "Nothing new there looked like a review. Paste reviews copied from "
            "Amazon, or plain text with a blank line between each one. Reviews "
            "already on this listing are skipped."
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
