"""
Background jobs with pollable progress.

Extraction and parsing take too long to hold an HTTP request open, so the route
starts a job and hands back an id. The browser polls /api/jobs/<id> and draws a
progress bar from what it finds.

In-memory and process-local by design: a job that dies with the server should
not leave a "running" record on disk. The app is shared by a small team, so jobs
that must not overlap (extractions: one Amazon login, one Rainforest limit) are
started in a lane and wait their turn — see `start(lane=…)`.
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
        self.waiting = False            # in a lane's line, not started yet
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
                "waiting": self.waiting,
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


class Phase:
    """One stretch of a job that runs several steps back to back.

    The pipeline's steps each count their own work (`set_total`, `step`), which
    would overshoot the bar if two of them shared a job. A Phase gives a step
    the same surface and maps its progress onto a slice of the parent — `start`
    to `end`, as fractions of the whole. Notes and failures go straight to the
    parent. The bar never moves backwards, even when a step raises its own total
    part-way through.
    """

    SCALE = 1000

    def __init__(self, job: Job, start: float, end: float):
        self.job, self.start, self.end = job, start, end
        self.total = 0
        self.done = 0
        self._lock = threading.Lock()
        with job._lock:
            job.total = self.SCALE
            job.done = max(job.done, round(start * self.SCALE))

    @property
    def notes(self) -> list[str]:
        return self.job.notes

    @property
    def failures(self) -> list[dict]:
        return self.job.failures

    def _push(self, label: str | None = None) -> None:
        share = min(1.0, self.done / self.total) if self.total else 0.0
        at = round((self.start + (self.end - self.start) * share) * self.SCALE)
        with self.job._lock:
            self.job.done = max(self.job.done, at)
            if label:
                self.job.label = label

    def set_total(self, total: int) -> None:
        with self._lock:
            self.total = total
            self._push()

    def step(self, label: str | None = None, count: int = 1) -> None:
        with self._lock:
            self.done += count
            self._push(label)

    def note(self, message: str) -> None:
        self.job.note(message)

    def fail_item(self, name: str, reason: str) -> None:
        self.job.fail_item(name, reason)


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


# Waiting lines. Jobs started with the same `lane` run one at a time, in the order
# they were started; everything else runs at once as before. A waiting job is
# still "running" as far as the browser is concerned — its label says it is
# waiting — so every progress bar shows the wait without knowing about lanes.
_LANES: dict[str, list[str]] = {}
_LANES_TURN = threading.Condition()


def _wait_label(ahead: int) -> str:
    if ahead == 1:
        return "Waiting for another extraction to finish…"
    return f"Waiting for {ahead} other extractions to finish…"


def _take_turn(job: Job, lane: str) -> None:
    """Join the lane's line and return once this job is at the front of it."""
    label = job.label
    with _LANES_TURN:
        line = _LANES.setdefault(lane, [])
        line.append(job.id)
        while line[0] != job.id:
            job.waiting = True
            job.label = _wait_label(line.index(job.id))
            _LANES_TURN.wait()
        job.waiting = False
        job.label = label


def _leave(job: Job, lane: str) -> None:
    with _LANES_TURN:
        line = _LANES.get(lane) or []
        if job.id in line:
            line.remove(job.id)
        _LANES_TURN.notify_all()


def start(label: str, fn, total: int = 0, lane: str | None = None) -> Job:
    """Run `fn(job)` on a background thread and return the Job immediately.

    With `lane`, the job waits its turn behind earlier jobs in the same lane.
    """
    job = Job(label, total)
    with _JOBS_LOCK:
        _JOBS[job.id] = job

    def runner():
        try:
            if lane:
                _take_turn(job, lane)
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
            if lane:
                _leave(job, lane)

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
