"""
Background jobs with pollable progress.

Extraction and parsing take too long to hold an HTTP request open, so the route
starts a job and hands back an id. The browser polls /api/jobs/<id> and draws a
progress bar from what it finds.

In-memory and process-local by design: this is a single-user local tool, and a
job that dies with the server should not leave a "running" record on disk.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime, timezone


class Job:
    """A unit of background work that reports how far along it is."""

    def __init__(self, label: str, total: int = 0):
        self.id = uuid.uuid4().hex[:12]
        self.label = label
        self.total = total
        self.done = 0
        self.state = "running"          # running | done | error
        self.error: str | None = None
        self.result = None
        self.notes: list[str] = []      # run-wide observations
        self.failures: list[dict] = []  # per-item problems
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.finished_at: str | None = None
        self._lock = threading.Lock()

    # -- called from inside the worker ------------------------------------
    def set_total(self, total: int) -> None:
        with self._lock:
            self.total = total

    def step(self, label: str | None = None, count: int = 1) -> None:
        with self._lock:
            self.done += count
            if label:
                self.label = label

    def note(self, message: str) -> None:
        """A run-wide observation, e.g. the reviews endpoint being unavailable."""
        with self._lock:
            if message and message not in self.notes:
                self.notes.append(message)

    def fail_item(self, name: str, reason: str) -> None:
        with self._lock:
            self.failures.append({"item": name, "reason": reason})

    # -- read by the API --------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "state": self.state,
                "label": self.label,
                "done": self.done,
                "total": self.total,
                "pct": round(100 * self.done / self.total) if self.total else 0,
                "notes": list(self.notes),
                "failures": list(self.failures),
                "error": self.error,
                "result": self.result,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def start(label: str, fn, total: int = 0) -> Job:
    """Run `fn(job)` on a background thread and return the Job immediately."""
    job = Job(label, total)
    with _JOBS_LOCK:
        _JOBS[job.id] = job

    def runner():
        try:
            job.result = fn(job)
            job.state = "done"
            job.label = "Finished"
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            job.state = "error"
            job.error = str(exc) or exc.__class__.__name__
            job.label = "Failed"
            traceback.print_exc()
        finally:
            job.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    threading.Thread(target=runner, daemon=True, name=f"job-{job.id}").start()
    return job


def get(job_id: str) -> Job | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def prune(keep: int = 40) -> None:
    """Drop the oldest finished jobs so a long session doesn't grow forever."""
    with _JOBS_LOCK:
        finished = sorted(
            (j for j in _JOBS.values() if j.state != "running"),
            key=lambda j: j.finished_at or "",
        )
        for job in finished[:max(0, len(finished) - keep)]:
            _JOBS.pop(job.id, None)
