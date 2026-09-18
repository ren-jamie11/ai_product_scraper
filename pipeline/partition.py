"""
Step 3 helper — splitting a big tag list into partitions that can be clustered in
parallel, without losing the comparisons that matter.

Clustering is a global judgement: "sturdy bowl" and "thick build" can only be
called one concern by a model that sees both. That is why grouping.py clusters a
list in one call for as long as it can. Past a few hundred tags one call stops
being possible — the answer runs for ten minutes and past the model's attention —
so the list has to be split. Splitting it alphabetically would put "heavy bowl"
and "solid-feeling weight" in different calls and no later step could reunite
them. Splitting it by *meaning* keeps them together: every tag is embedded, and
the list is bisected on those vectors until each piece is small enough. Tags that
would have been judged together mostly still are; the few concepts that straddle
a boundary are reunited by the merge call in grouping.py.

Everything here is deterministic. The bisection seeds from the geometry of the
vectors, not a random draw, so the same tags always partition the same way and
two runs of one parse can be compared.

If the embeddings endpoint is unusable the caller falls back to alphabetical
slices and says so in the log — a worse partition is still better than no run.
"""

from __future__ import annotations

import math
import random
import time

import numpy as np

import config
from pipeline import tagging


class PartitionError(Exception):
    """A problem that stops the run outright (a rejected key, no credit)."""


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

BATCH = 500


def embed(texts: list[str]) -> np.ndarray | None:
    """Unit-length embeddings for `texts`, one row each, or None if the endpoint
    could not be used (the caller then partitions without them).

    Only a rejected key or an empty account is raised: those stop the grouping
    calls too. An unknown embedding model or a flaky connection degrades to the
    alphabetical fallback rather than failing a run over a helper step.
    """
    if not texts:
        return np.zeros((0, config.GROUP_EMBED_DIMENSIONS), dtype=np.float32)

    client = tagging.client()
    rows: list[list[float] | None] = [None] * len(texts)
    attempts = max(1, config.OPENAI_MAX_RETRIES + 1)

    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        response = None
        for attempt in range(attempts):
            try:
                response = client.embeddings.create(
                    model=config.GROUP_EMBED_MODEL,
                    input=chunk,
                    dimensions=config.GROUP_EMBED_DIMENSIONS,
                )
                break
            except Exception as exc:
                if tagging._status(exc) in (401, 402, 403) or "insufficient_quota" in str(exc):
                    raise PartitionError(
                        tagging._fatal(exc, config.GROUP_EMBED_MODEL, what="grouped")
                        or str(exc)
                    ) from exc
                if not tagging._retryable(exc) or attempt == attempts - 1:
                    return None
                time.sleep((2 ** attempt) + random.uniform(0, 0.5))
        if response is None:
            return None
        for item in response.data:
            rows[start + item.index] = item.embedding

    if any(r is None for r in rows):
        return None
    vectors = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------

def slices(count: int, target: int) -> list[list[int]]:
    """`count` positions cut into the fewest near-equal runs of at most `target`.
    The fallback when there are no vectors: the caller orders the tags first."""
    if count <= 0:
        return []
    pieces = max(1, math.ceil(count / target))
    base, extra = divmod(count, pieces)
    out, start = [], 0
    for i in range(pieces):
        size = base + (1 if i < extra else 0)
        out.append(list(range(start, start + size)))
        start += size
    return out


def partition(vectors: np.ndarray, target: int, minimum: int) -> list[list[int]]:
    """Index groups over the rows of `vectors`, each at most `target` long and as
    semantically coherent as bisecting k-means makes them. Pieces smaller than
    `minimum` are folded into the nearest sibling that still has room.

    Returned largest first, each group's indices ascending, so the order is a
    function of the vectors alone.
    """
    count = len(vectors)
    if count == 0:
        return []
    if count <= target:
        return [list(range(count))]

    pending = [np.arange(count)]
    done: list[np.ndarray] = []
    while pending:
        idx = pending.pop()
        if len(idx) <= target:
            done.append(idx)
            continue
        left, right = _bisect(vectors[idx])
        if len(left) == 0 or len(right) == 0:
            # Every vector identical (or as good as): nothing to bisect on.
            half = len(idx) // 2
            left, right = np.arange(half), np.arange(half, len(idx))
        pending.append(idx[left])
        pending.append(idx[right])

    done = _fold_small(done, vectors, minimum, target)
    done.sort(key=lambda p: (-len(p), int(p.min())))
    return [sorted(int(i) for i in p) for p in done]


def _bisect(x: np.ndarray, iterations: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """Two-means on `x` (rows), seeded from the geometry so it is deterministic:
    the point farthest from the mean, then the point farthest from that one."""
    mean = x.mean(axis=0)
    first = x[int(np.argmax(((x - mean) ** 2).sum(axis=1)))]
    second = x[int(np.argmax(((x - first) ** 2).sum(axis=1)))]
    centers = np.stack([first, second]).astype(np.float32)

    labels = None
    for _ in range(iterations):
        distances = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        new = distances.argmin(axis=1)
        if labels is not None and np.array_equal(new, labels):
            break
        labels = new
        for k in range(2):
            members = labels == k
            if members.any():
                centers[k] = x[members].mean(axis=0)

    return np.flatnonzero(labels == 0), np.flatnonzero(labels == 1)


def _fold_small(parts: list[np.ndarray], vectors: np.ndarray, minimum: int,
                target: int) -> list[np.ndarray]:
    """Merge every piece under `minimum` into its nearest sibling with room.
    A piece with nowhere to go stays as it is."""
    kept: list[np.ndarray] = []
    pool = sorted(parts, key=len)
    while pool:
        small = pool.pop(0)
        if len(small) >= minimum or not pool:
            kept.append(small)
            continue
        centroid = vectors[small].mean(axis=0)
        best, best_distance = None, None
        for i, other in enumerate(pool):
            if len(other) + len(small) > target:
                continue
            distance = float(((vectors[other].mean(axis=0) - centroid) ** 2).sum())
            if best is None or distance < best_distance:
                best, best_distance = i, distance
        if best is None:
            kept.append(small)
            continue
        merged = np.concatenate([pool.pop(best), small])
        # Re-insert in size order so a merged piece can absorb further small ones.
        position = 0
        while position < len(pool) and len(pool[position]) < len(merged):
            position += 1
        pool.insert(position, merged)
    return kept
