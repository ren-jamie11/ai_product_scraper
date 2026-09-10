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
    group["runs"] = list_runs(slug)
    return group


def list_groups() -> list[dict]:
    groups = []
    for child in data_root().iterdir():
        if not child.is_dir():
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

def list_runs(slug: str) -> list[dict]:
    """Runs for a group, newest first. Run ids sort correctly as strings."""
    directory = group_dir(slug)
    if not directory.is_dir():
        return []

    runs = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name, reverse=True):
        if not child.is_dir():
            continue
        log = read_json(child / "run_log.json", {}) or {}
        runs.append({
            "run_id": child.name,
            "started_at": log.get("started_at"),
            "asins_ok": log.get("asins_ok"),
            "asins_total": log.get("asins_total"),
            "reviews_total": log.get("reviews_total"),
            "parses": [p.name for p in sorted(child.glob("parse-*"), reverse=True) if p.is_dir()],
        })
    return runs


def _claim_run_dir(slug: str) -> tuple[str, Path]:
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


def create_run(slug: str, inputs: list[dict]) -> str:
    """Start a new run folder for a group and return its run id."""
    if not group_file(slug).exists():
        raise StorageError("That group no longer exists.")

    run_id, directory = _claim_run_dir(slug)
    write_json(directory / "run_log.json", {
        "run_id": run_id,
        "started_at": now_iso(),
        "state": "running",
        "asins_total": len(inputs),
        "inputs": inputs,
    })
    return run_id


def load_run(slug: str, run_id: str) -> dict:
    """Return extracted.json with edits.json applied on top.

    extracted.json and the raw payloads are never mutated, so the machine
    output stays visible next to whatever the user corrected by hand.
    """
    directory = run_dir(slug, run_id)
    extracted = read_json(directory / "extracted.json")
    if not extracted:
        raise StorageError("That run has no extracted data yet.")

    edits = read_json(directory / "edits.json", {}) or {}
    for product in extracted.get("products", []):
        override = edits.get(product.get("asin"))
        if override:
            product.update(override)
            product["edited_fields"] = sorted(override.keys())
    return extracted
