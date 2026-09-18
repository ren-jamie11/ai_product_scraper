"""
Local file storage: competitor groups, their runs, and safe JSON read/write.

Layout on disk:

    data/<group-slug>/group.json
    data/<group-slug>/<run-id>/raw/<ASIN>.product.json
    data/<group-slug>/<run-id>/extracted.json
    data/<group-slug>/<run-id>/edits.json
    data/<group-slug>/<run-id>/run_log.json
    data/<group-slug>/<run-id>/parse-<timestamp>/...

Runs are timestamped and never overwritten, so re-extracting a group keeps the
whole history.

Deleting moves a folder into a sibling `_trash/` rather than removing it — every
run cost real Rainforest credits, so a misclick should be recoverable from the
file browser. Anything whose name starts with `_` is invisible to the listings,
which is what keeps `_trash/` from showing up as a group or a run.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import config

_NON_SLUG = re.compile(r"[^a-z0-9]+")

# The overlay marker for an ASIN the user removed from a run. See "Edits
# overlay" below for the shape of edits.json.
DELETED_KEY = "__deleted"
NO_REVIEWS_WARNING = "Rainforest returned no reviews for this listing."


class StorageError(Exception):
    """A problem the user can act on. The message is shown in the UI verbatim."""


# ---------------------------------------------------------------------------
# Paths and small helpers
# ---------------------------------------------------------------------------

def data_root() -> Path:
    root = Path(config.DATA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def group_dir(slug: str) -> Path:
    return data_root() / slug


def group_file(slug: str) -> Path:
    return group_dir(slug) / "group.json"


def run_dir(slug: str, run_id: str) -> Path:
    return group_dir(slug) / run_id


def is_hidden(name: str) -> bool:
    """`_trash` and anything else underscored is bookkeeping, not user data."""
    return name.startswith("_") or name.startswith(".")


def slugify(name: str) -> str:
    return _NON_SLUG.sub("-", (name or "").strip().lower()).strip("-")[:80]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------

def read_json(path, default=None):
    """Read JSON, returning `default` if the file is missing or unparseable."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return default


def write_json(path, obj) -> Path:
    """Write JSON through a temp file in the same directory.

    os.replace is atomic, so an interrupted run can never leave a half-written
    file that later reads back as corrupt.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def create_group(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise StorageError("Give the group a name first.")
    slug = slugify(name)
    if not slug:
        raise StorageError(
            "That name has no letters or numbers in it. Try something like "
            "\"Vintage Gold Picture Frames\"."
        )
    if group_file(slug).exists():
        raise StorageError(f'A group called "{name}" already exists.')

    group = {"name": name, "slug": slug, "created_at": now_iso()}
    write_json(group_file(slug), group)
    return group


def get_group(slug: str) -> dict:
    group = read_json(group_file(slug))
    if not group:
        raise StorageError("That group no longer exists.")
    group["runs"] = list_runs(slug, with_stats=True)
    return group


def list_groups() -> list[dict]:
    groups = []
    for child in data_root().iterdir():
        if not child.is_dir() or is_hidden(child.name):
            continue
        group = read_json(child / "group.json")
        if not group:
            continue
        runs = list_runs(child.name)
        group["run_count"] = len(runs)
        group["last_run"] = runs[0]["run_id"] if runs else None
        groups.append(group)
    groups.sort(key=lambda g: g.get("created_at") or "", reverse=True)
    return groups


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def list_runs(slug: str, with_stats: bool = False) -> list[dict]:
    """Runs for a group, newest first. Run ids sort correctly as strings.

    `with_stats` recomputes the ASIN and review counts from the current data
    rather than reading them out of run_log.json, so a run that has been edited
    reports what it now holds. It costs one extracted.json read per run, which
    is why the groups list — needing only how many runs there are — leaves it
    off.
    """
    directory = group_dir(slug)
    if not directory.is_dir():
        return []

    runs = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name, reverse=True):
        if not child.is_dir() or is_hidden(child.name):
            continue
        log = read_json(child / "run_log.json", {}) or {}
        run = {
            "run_id": child.name,
            "label": (read_json(child / "run_meta.json", {}) or {}).get("label"),
            "started_at": log.get("started_at"),
            "asins_ok": log.get("asins_ok"),
            "asins_total": log.get("asins_total"),
            "reviews_total": log.get("reviews_total"),
            "compacted_from": log.get("compacted_from"),
            "parses": list_parses(slug, child.name),
        }
        if with_stats:
            run.update(_live_stats(slug, child.name))
        runs.append(run)
    return runs


def _live_stats(slug: str, run_id: str) -> dict:
    """Current counts for a run, or {} if it never got as far as writing data.

    Imported here rather than at module scope: normalize imports config, and
    keeping storage free of that cycle matters more than the tidiness.
    """
    from pipeline import normalize

    try:
        products = load_run(slug, run_id).get("products", [])
    except StorageError:
        return {}   # an extraction that died before writing — keep the log's view

    summary = normalize.summarize(products)
    return {key: summary[key] for key in ("asins_ok", "asins_total", "reviews_total")}


def claim_run_dir(slug: str) -> tuple[str, Path]:
    """Create a fresh run folder, never reusing one.

    Run ids are second-granularity timestamps, so two runs started in the same
    second would otherwise land in the same folder and overwrite each other.
    exist_ok=False makes the claim itself the collision check.
    """
    base = new_run_id()
    candidate, suffix = base, 2
    while True:
        directory = run_dir(slug, candidate)
        try:
            (directory / "raw").mkdir(parents=True, exist_ok=False)
            return candidate, directory
        except FileExistsError:
            candidate = f"{base}-{suffix}"
            suffix += 1


def claim_parse_dir(slug: str, run_id: str) -> tuple[str, Path]:
    """Create a fresh parse folder inside a run, never reusing one.

    Same contract as claim_run_dir: the mkdir itself is the collision check, so
    two parses started in the same second can't overwrite each other. Keeping
    every parse means you can re-tag with a changed prompt and still compare the
    old output against the new.
    """
    directory = run_dir(slug, run_id)
    if not (directory / "extracted.json").exists():
        raise StorageError("That run has no extracted data to parse.")

    base = f"parse-{new_run_id()}"
    candidate, suffix = base, 2
    while True:
        target = directory / candidate
        try:
            target.mkdir(parents=False, exist_ok=False)
            return candidate, target
        except FileExistsError:
            candidate = f"{base}-{suffix}"
            suffix += 1


def list_parses(slug: str, run_id: str) -> list[dict]:
    """Parses for a run, newest first: `{id, grouped}`.

    `grouped` is whether clusters.json exists. Tags alone are an intermediate artifact —
    only a grouped parse has results worth opening, so the UI needs to know which pills
    are links without fetching every parse.
    """
    directory = run_dir(slug, run_id)
    if not directory.is_dir():
        return []
    return [
        {"id": p.name, "grouped": (p / "clusters.json").is_file()}
        for p in sorted(directory.glob("parse-*"), reverse=True) if p.is_dir()
    ]


def create_run(slug: str, inputs: list[dict]) -> str:
    """Start a new run folder for a group and return its run id."""
    if not group_file(slug).exists():
        raise StorageError("That group no longer exists.")

    run_id, directory = claim_run_dir(slug)
    write_json(directory / "run_log.json", {
        "run_id": run_id,
        "started_at": now_iso(),
        "state": "running",
        "asins_total": len(inputs),
        "inputs": inputs,
    })
    return run_id


def load_run(slug: str, run_id: str, include_deleted: bool = False) -> dict:
    """Return extracted.json with edits.json applied on top.

    extracted.json and the raw payloads are never mutated, so the machine
    output stays visible next to whatever the user corrected by hand.

    Removed ASINs are filtered out by default — tagging must never pick one up.
    The run view asks for them back so it can offer to restore them.
    """
    directory = run_dir(slug, run_id)
    extracted = read_json(directory / "extracted.json")
    if not extracted:
        raise StorageError("That run has no extracted data yet.")

    edits = read_json(directory / "edits.json", {}) or {}
    products = []
    for product in extracted.get("products", []):
        override = dict(edits.get(product.get("asin")) or {})
        deleted = bool(override.pop(DELETED_KEY, False))
        if deleted and not include_deleted:
            continue
        if override:
            product.update(override)
            product["edited_fields"] = sorted(override.keys())
        # Once reviews exist (pasted by hand, usually), the fetch-time note that
        # Rainforest had none is stale. Delete them all and it comes back.
        if product.get("reviews") and product.get("warnings"):
            product["warnings"] = [w for w in product["warnings"] if w != NO_REVIEWS_WARNING]
        product["deleted"] = deleted
        products.append(product)

    extracted["products"] = products
    return extracted


# ---------------------------------------------------------------------------
# Edits overlay
#
# edits.json is a diff against extracted.json, not a copy of it:
#
#     {"B08P5LPZFJ": {"title": "...", "reviews": [...]},
#      "B0BADBADBA": {"__deleted": true}}
#
# Patches merge field-by-field. A field sent as null is removed from the
# overlay, which is how a value goes back to whatever Rainforest returned.
# ---------------------------------------------------------------------------

def read_edits(slug: str, run_id: str) -> dict:
    return read_json(run_dir(slug, run_id) / "edits.json", {}) or {}


def merge_edits(slug: str, run_id: str, patch: dict) -> dict:
    """Apply `{asin: {field: value}}` on top of the stored overlay."""
    directory = run_dir(slug, run_id)
    if not (directory / "extracted.json").exists():
        raise StorageError("That run no longer exists.")
    if not isinstance(patch, dict) or not patch:
        raise StorageError("Nothing to save.")

    edits = read_edits(slug, run_id)
    for asin, fields in patch.items():
        if not isinstance(fields, dict):
            raise StorageError(f"Malformed edit for {asin}.")
        entry = dict(edits.get(asin) or {})
        for key, value in fields.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        # An overlay entry with nothing in it is the same as no entry at all.
        if entry:
            edits[asin] = entry
        else:
            edits.pop(asin, None)

    write_json(directory / "edits.json", edits)
    return edits


def clear_edits(slug: str, run_id: str, asin: str) -> dict:
    """Drop every override for one ASIN — "restore original"."""
    directory = run_dir(slug, run_id)
    edits = read_edits(slug, run_id)
    edits.pop(asin, None)
    write_json(directory / "edits.json", edits)
    return edits


# ---------------------------------------------------------------------------
# Run metadata
#
# run_meta.json is to run_log.json what edits.json is to extracted.json: the
# things you set by hand, kept apart from the machine's record of the fetch.
# Today that is only a label, which is why run ids stay timestamps — the
# ordering and compaction's tie-break both depend on that.
# ---------------------------------------------------------------------------

def read_run_meta(slug: str, run_id: str) -> dict:
    return read_json(run_dir(slug, run_id) / "run_meta.json", {}) or {}


def set_run_label(slug: str, run_id: str, label: str) -> dict:
    """Name a run, or clear the name by passing an empty one."""
    directory = run_dir(slug, run_id)
    if not directory.is_dir() or is_hidden(run_id):
        raise StorageError("That run no longer exists.")

    meta = read_run_meta(slug, run_id)
    label = (label or "").strip()[:120]
    if label:
        meta["label"] = label
    else:
        meta.pop("label", None)

    write_json(directory / "run_meta.json", meta)
    return meta


# ---------------------------------------------------------------------------
# Deleting — into _trash/, never gone
# ---------------------------------------------------------------------------

def _move_to_trash(source: Path, trash_root: Path) -> Path:
    """Move a folder under `trash_root`, suffixing if that name is taken."""
    trash_root.mkdir(parents=True, exist_ok=True)
    target, suffix = trash_root / source.name, 2
    while target.exists():
        target = trash_root / f"{source.name}-{suffix}"
        suffix += 1
    os.replace(source, target)
    return target


def trash_run(slug: str, run_id: str) -> Path:
    """Move one run into data/<slug>/_trash/."""
    source = run_dir(slug, run_id)
    if not source.is_dir() or is_hidden(run_id):
        raise StorageError("That run no longer exists.")
    return _move_to_trash(source, group_dir(slug) / "_trash")


def trash_group(slug: str) -> Path:
    """Move a whole group, runs and all, into data/_trash/."""
    source = group_dir(slug)
    if not source.is_dir() or is_hidden(slug):
        raise StorageError("That group no longer exists.")
    return _move_to_trash(source, data_root() / "_trash")
