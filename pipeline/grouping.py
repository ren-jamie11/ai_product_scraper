"""
Step 3 — turning thousands of loose tags into a few readable product concepts.

One OpenAI call per tag list, for as long as the list fits. Clustering is a global
judgement: deciding that "sturdy bowl" and "thick build" are one customer concern
means comparing them against every other tag at once, so a list at or under
GROUP_SINGLE_CALL_MAX goes to the model whole. Past that a single call stops being
possible — measured 2026-09-18, the answer grows ~27 output tokens per tag and the
model writes ~65 a second, so 1,300 tags hit the SDK timeout and a 50-ASIN category
would run a quarter of an hour. A long list is therefore partitioned *by meaning*
(partition.py embeds the tags and bisects them), each partition is clustered in
parallel by the same prompt at the size the prompt was validated at, and one merge
call — which sees every partial cluster with every one of its tags, not just the
titles — joins the concepts that straddled a boundary. Small lists never take that
path, so their output is exactly what it always was.

Three layers survive into clusters.json, and the middle one is the point:

    occurrences   every mention, with body_id / asin / body_type
    unique_tags   one per distinct string, holding its occ_ids
    clusters      one per concept, holding uids

Grouping happens on the top two layers, so no matter how much collapsing happens,
every tag still points at the exact review or listing that produced it. That is
what makes hover-to-source work in Phase 5, and Phase 6's sentence highlighting
needs the verbatim string, so `display` is never normalised.

The rules live in prompts/group_tags.md, not here. This module is the machinery:
fan out over the lists, validate that every tag was placed exactly once, repair
what the model dropped, and count.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from pipeline import partition, settings, storage, tagging

PROMPT_PATH = Path(__file__).parent / "prompts" / "group_tags.md"
MERGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "merge_clusters.md"

# The three lists worth clustering, in the order they appear in the output. Search
# terms and usage keywords are deliberately absent: they are 1-5 word keywords that
# Phase 5 shows as flat frequency lists, and the usage facets already group them.
LISTS = ("features", "complaints", "assembly_maintenance")

TITLES = {
    "features": "Product Features & Benefits",
    "complaints": "Complaints",
    "assembly_maintenance": "Assembly, Care & Maintenance",
}

# strict mode wants every property in `required` and additionalProperties false at
# every level. `indices` rather than tag strings is a deliberate choice: the model
# cannot corrupt text it never writes.
SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "indices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "description", "indices"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}


class GroupingError(Exception):
    """A problem that stops the whole grouping run. Shown to the user verbatim."""


# ---------------------------------------------------------------------------
# Prompt and settings
# ---------------------------------------------------------------------------

def load_prompt(path: Path = PROMPT_PATH) -> tuple[str, str]:
    """The instruction text and a hash of it, for cluster_log.json."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GroupingError(
            f"The grouping instructions are missing. Expected them at {path}."
        ) from None
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_merge_prompt() -> tuple[str, str]:
    """The merge instructions used on a partitioned list, and their hash."""
    return load_prompt(MERGE_PROMPT_PATH)


def group_choice() -> dict:
    """Model and effort in force, resolved once so a run stays self-consistent."""
    return {
        "model": settings.resolve("group_model"),
        "reasoning": settings.resolve("group_reasoning"),
    }


# ---------------------------------------------------------------------------
# Layer 1 and 2 — occurrences and unique tags
# ---------------------------------------------------------------------------

def collect(tagged: dict, key: str) -> tuple[list[dict], list[dict]]:
    """Every mention of every tag in one list, plus the distinct strings.

    Built by walking bodies, so a tag can never exist without a body_id to point
    back at. Dedup is on the exact trimmed string and nothing more: the plan's
    article/plural fold was measured at 0-2% on real data, which does not buy the
    risk of folding "sturdy pot" into "sturdy pots" on a product sold in pairs.
    """
    occurrences: list[dict] = []
    uniques: list[dict] = []
    by_text: dict[str, dict] = {}

    for body in tagged.get("bodies") or []:
        if body.get("status") != "ok":
            continue
        for raw in (body.get("tags") or {}).get(key) or []:
            text = (raw or "").strip()
            if not text:
                continue

            occ_id = f"o_{len(occurrences):04d}"
            occurrences.append({
                "occ_id": occ_id,
                "text": text,
                "body_id": body["body_id"],
                "asin": body["asin"],
                "body_type": body["type"],
            })

            unique = by_text.get(text)
            if unique is None:
                unique = {"uid": f"u_{len(uniques):03d}", "display": text, "occ_ids": []}
                by_text[text] = unique
                uniques.append(unique)
            unique["occ_ids"].append(occ_id)

    return occurrences, uniques


def _counts(uids: list[str], by_uid: dict, by_occ: dict) -> dict:
    """Cluster metrics, computed from occurrences rather than unique tags.

    "10 tags · 5 listings · 3 reviews" has to mean ten actual mentions, otherwise a
    tag that thirty reviewers all phrased identically would count once and the
    sort order would be wrong.
    """
    total = 0
    listings, reviews, asins = set(), set(), set()
    for uid in uids:
        for occ_id in by_uid[uid]["occ_ids"]:
            occ = by_occ[occ_id]
            total += 1
            asins.add(occ["asin"])
            if occ["body_type"] == "listing":
                listings.add(occ["body_id"])
            else:
                reviews.add(occ["body_id"])
    return {
        "total_tags": total,
        "unique_listings": len(listings),
        "unique_reviews": len(reviews),
        "unique_asins": len(asins),
    }


# ---------------------------------------------------------------------------
# One call
# ---------------------------------------------------------------------------

def build_input(key: str, uniques: list[dict], product: str,
                prior_titles: list[str]) -> str:
    """The user message for one list."""
    lines = [
        f"PRODUCT CATEGORY: {product}",
        f"LIST: {key}",
    ]
    if prior_titles:
        lines += [
            "",
            "PRIOR CLUSTER TITLES (reuse a title exactly when the theme matches; "
            "never force a tag into one, and never emit a title with no tags)",
            *(f"- {title}" for title in prior_titles),
        ]
    lines += [
        "",
        f"TAGS ({len(uniques)}) — every index below must appear in exactly one cluster",
    ]
    lines += [f"{i}. {u['display']}" for i, u in enumerate(uniques)]
    return "\n".join(lines)


def _call(prompt: str, user_input: str, choice: dict, *,
          schema: dict = SCHEMA, schema_name: str = "tag_clusters",
          result_key: str = "clusters", max_output_tokens: int | None = None,
          max_tokens_setting: str = "GROUP_MAX_OUTPUT_TOKENS",
          error_cls: type[Exception] = GroupingError,
          tag_count: int | None = None) -> tuple[list[dict], dict]:
    """One request. Returns (items, usage) or raises.

    Parametrised because the theme step (themes.py) makes the same kind of call
    with a different schema and a different error class; the defaults keep this
    module's own behaviour unchanged. `tag_count` only sharpens the cut-off
    message, so it can say how many tokens this list was always going to need.
    """
    if max_output_tokens is None:
        max_output_tokens = config.GROUP_MAX_OUTPUT_TOKENS
    kwargs = {
        "model": choice["model"],
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": max_output_tokens,
        # A big list runs for minutes; the SDK's 600 s default would cut it off
        # and then retry it, which is the most expensive way to get nothing.
        "timeout": config.OPENAI_LONG_CALL_TIMEOUT,
    }
    if choice["reasoning"]:
        kwargs["reasoning"] = {"effort": choice["reasoning"]}

    try:
        response = tagging.client().responses.create(**kwargs)
    except Exception as exc:
        # Some models have no reasoning knob. Drop it once and try again.
        if "reasoning" in kwargs and "reasoning" in str(exc).lower():
            kwargs.pop("reasoning")
            response = tagging.client().responses.create(**kwargs)
        else:
            raise

    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            hint = ""
            if tag_count:
                hint = (f" (this list has {tag_count} tags; about "
                        f"{expected_output_tokens(tag_count):,} were expected)")
            raise error_cls(
                f"The answer was cut off at {max_output_tokens:,} tokens{hint}. "
                f"Raise {max_tokens_setting} in config.py."
            )
        raise error_cls(f"OpenAI returned an incomplete answer ({reason}).")

    text = (getattr(response, "output_text", None) or "").strip()
    if not text:
        raise error_cls("OpenAI returned an empty answer.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise error_cls("OpenAI returned something that wasn't valid JSON.") from None

    usage = getattr(response, "usage", None)
    return payload.get(result_key) or [], {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }


def call_with_retries(prompt: str, user_input: str, choice: dict, *, what: str = "grouped",
                      error_cls: type[Exception] = GroupingError,
                      **call_kwargs) -> tuple[list[dict] | None, dict, str]:
    """`_call`, retrying the errors worth retrying.

    Returns (items, usage, last_error). `items` is None when every attempt failed
    with something that isn't fatal — a bad answer or a flaky connection — so the
    caller can mark that one list as failed and keep going. Fatal problems (a
    rejected key, an unknown model, no credit) are raised as `error_cls` because
    retrying them for the next list helps nobody.
    """
    attempts = max(1, config.OPENAI_MAX_RETRIES + 1)
    last = "unknown error"
    usage = {"input_tokens": 0, "output_tokens": 0}

    for attempt in range(attempts):
        try:
            raw, usage = _call(prompt, user_input, choice, error_cls=error_cls, **call_kwargs)
            return raw, usage, ""
        except error_cls as exc:
            return None, usage, str(exc)      # a bad answer, not a flaky connection
        except Exception as exc:
            fatal = tagging._fatal(exc, choice["model"], what=what)
            if fatal:
                raise error_cls(fatal) from exc
            last = str(exc) or exc.__class__.__name__
            if not tagging._retryable(exc) or attempt == attempts - 1:
                break
            time.sleep((2 ** attempt) + random.uniform(0, 0.5))

    return None, usage, last


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(raw_clusters: list[dict], uniques: list[dict]) -> tuple[list[dict], list[dict]]:
    """Force the answer back onto the invariant: every tag in exactly one cluster.

    The model is asked for this and usually delivers it, but "usually" is not a
    guarantee we can build counts on. Every repair is returned so it lands in
    cluster_log.json — a tag is never silently dropped or silently duplicated.
    """
    total = len(uniques)
    seen: set[int] = set()
    clusters: list[dict] = []
    repairs: list[dict] = []

    for cluster in raw_clusters:
        if not isinstance(cluster, dict):
            continue
        title = (cluster.get("title") or "").strip()
        description = (cluster.get("description") or "").strip()

        kept: list[int] = []
        for index in cluster.get("indices") or []:
            if not isinstance(index, int) or not 0 <= index < total:
                repairs.append({"kind": "out_of_range", "index": index, "cluster": title})
                continue
            if index in seen:
                repairs.append({
                    "kind": "duplicate",
                    "tag": uniques[index]["display"],
                    "cluster": title,
                    "note": "kept the first placement",
                })
                continue
            seen.add(index)
            kept.append(index)

        if not kept:
            continue                    # an empty cluster is not a cluster
        clusters.append({
            "title": title or uniques[kept[0]]["display"].title(),
            "description": description,
            "indices": kept,
        })

    # Anything the model forgot becomes its own cluster rather than vanishing. A
    # visible singleton with a plain title is a much better failure than a tag that
    # silently stops existing and quietly breaks sum(total_tags) == len(occurrences).
    for index in range(total):
        if index in seen:
            continue
        display = uniques[index]["display"]
        repairs.append({"kind": "orphan", "tag": display, "note": "made a singleton"})
        clusters.append({
            "title": display[:60].title(),
            "description": "",
            "indices": [index],
        })

    return clusters, repairs


# ---------------------------------------------------------------------------
# One list, end to end
# ---------------------------------------------------------------------------

def group_list(key: str, uniques: list[dict], occurrences: list[dict], product: str,
               prompt: str, choice: dict, prior_titles: list[str],
               progress=None) -> dict:
    """Cluster one tag list, retrying the errors worth retrying.

    A list that fits (GROUP_SINGLE_CALL_MAX) goes to the model in one call, as it
    always has. A longer one is partitioned by meaning, each partition clustered
    in parallel by the same prompt, and the partial clusters joined by one merge
    call. `progress(label)` is called as partitions finish, for the job bar.
    """
    if len(uniques) <= config.GROUP_SINGLE_CALL_MAX:
        return _group_single(key, uniques, occurrences, product, prompt, choice, prior_titles)
    return _group_partitioned(key, uniques, occurrences, product, prompt, choice,
                              prior_titles, progress)


def _failed(key: str, error: str, usage: dict, **extra) -> dict:
    return {"key": key, "title": TITLES[key], "status": "failed", "error": error,
            "clusters": [], "repairs": [], "usage": usage, **extra}


def _finish(key: str, clusters: list[dict], uniques: list[dict], occurrences: list[dict],
            repairs: list[dict], usage: dict, **extra) -> dict:
    """Turn validated clusters (global indices) into the section clusters.json holds."""
    by_uid = {u["uid"]: u for u in uniques}
    by_occ = {o["occ_id"]: o for o in occurrences}
    built = []
    for cluster in clusters:
        uids = [uniques[i]["uid"] for i in cluster["indices"]]
        built.append({
            "title": cluster["title"],
            "description": cluster["description"],
            "uids": uids,
            **_counts(uids, by_uid, by_occ),
        })
        # On a partitioned list, how many partial clusters the merge joined into
        # this one — so a hand review can go straight to the merge decisions.
        if cluster.get("members", 1) > 1:
            built[-1]["joined_from"] = cluster["members"]

    # Largest first, so the biggest customer concern is the first thing read. Ties
    # break on title for a stable file that diffs cleanly between two runs.
    built.sort(key=lambda c: (-c["total_tags"], c["title"].lower()))
    for number, cluster in enumerate(built, 1):
        cluster["cluster_id"] = f"g_{number:02d}"
        cluster["number"] = number

    return {"key": key, "title": TITLES[key], "status": "ok", "error": None,
            "clusters": built, "repairs": repairs, "usage": usage, **extra}


def _group_single(key: str, uniques: list[dict], occurrences: list[dict], product: str,
                  prompt: str, choice: dict, prior_titles: list[str]) -> dict:
    """The whole list in one call: every tag judged against every other."""
    user_input = build_input(key, uniques, product, prior_titles)
    raw, usage, last = call_with_retries(prompt, user_input, choice, tag_count=len(uniques))
    if raw is None:
        return _failed(key, last, usage, mode="single")

    clusters, repairs = validate(raw, uniques)
    return _finish(key, clusters, uniques, occurrences, repairs, usage, mode="single")


def _add_usage(total: dict, more: dict) -> None:
    total["input_tokens"] += more.get("input_tokens", 0) or 0
    total["output_tokens"] += more.get("output_tokens", 0) or 0


def _group_partitioned(key: str, uniques: list[dict], occurrences: list[dict], product: str,
                       prompt: str, choice: dict, prior_titles: list[str],
                       progress=None) -> dict:
    """Partition by meaning → cluster each partition in parallel → merge.

    The clustering prompt is the same one the single call uses, on a list that is
    the size the single call was validated at. The only new judgement is the merge,
    and it sees every partial cluster with every one of its tags.
    """
    title = TITLES[key]
    texts = [u["display"] for u in uniques]
    notes: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}

    try:
        vectors = partition.embed(texts)
    except partition.PartitionError as exc:
        raise GroupingError(str(exc)) from exc

    if vectors is None:
        notes.append(f"{title}: the embeddings endpoint was unavailable, so the list was "
                     f"partitioned alphabetically instead of by meaning.")
        order = sorted(range(len(uniques)), key=lambda i: texts[i].lower())
        parts = [[order[j] for j in piece]
                 for piece in partition.slices(len(uniques), config.GROUP_PARTITION_TARGET)]
    else:
        parts = partition.partition(vectors, config.GROUP_PARTITION_TARGET,
                                    config.GROUP_PARTITION_MIN)

    outcomes: list[dict | None] = [None] * len(parts)
    lock = threading.Lock()
    finished = [0]

    def work(number: int) -> None:
        indices = parts[number]
        subset = [uniques[i] for i in indices]
        user_input = build_input(key, subset, product, prior_titles)
        raw, part_usage, last = call_with_retries(prompt, user_input, choice,
                                                  tag_count=len(subset))
        if raw is None:
            outcome = {"status": "failed", "error": last, "usage": part_usage}
        else:
            clusters, repairs = validate(raw, subset)
            for cluster in clusters:
                cluster["indices"] = [indices[i] for i in cluster["indices"]]
            for repair in repairs:
                repair["partition"] = number + 1
            outcome = {"status": "ok", "clusters": clusters, "repairs": repairs,
                       "usage": part_usage}
        with lock:
            outcomes[number] = outcome
            finished[0] += 1
            if progress:
                progress(f"{title}: {finished[0]} of {len(parts)} partitions clustered")

    workers = max(1, min(config.OPENAI_WORKERS, len(parts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, range(len(parts))))

    for outcome in outcomes:
        if outcome:
            _add_usage(usage, outcome["usage"])

    detail = {
        "count": len(parts),
        "sizes": [len(p) for p in parts],
        "by_meaning": vectors is not None,
    }
    failed = [(i, o) for i, o in enumerate(outcomes) if not o or o["status"] == "failed"]
    if failed:
        i, outcome = failed[0]
        error = (f"Partition {i + 1} of {len(parts)} ({len(parts[i])} tags) could not be "
                 f"clustered: {(outcome or {}).get('error') or 'no answer'}")
        if len(failed) > 1:
            error += f" ({len(failed)} partitions failed in all.)"
        return _failed(key, error, usage, mode="partitioned", partitions=detail, notes=notes)

    partial = [c for o in outcomes for c in o["clusters"]]
    repairs = [r for o in outcomes for r in o["repairs"]]
    if progress:
        progress(f"{title}: joining {len(partial)} partial clusters")

    merged, merge_repairs, merge_usage, error = _merge(key, partial, uniques, product,
                                                       choice, prior_titles)
    _add_usage(usage, merge_usage)
    if merged is None:
        return _failed(key, f"Joining the partial clusters failed: {error}", usage,
                       mode="partitioned", partitions=detail, notes=notes)

    detail.update({
        "clusters_before_merge": len(partial),
        "clusters_after_merge": len(merged),
        "joins": sum(1 for m in merged if m["members"] > 1),
    })
    return _finish(key, merged, uniques, occurrences, repairs + merge_repairs, usage,
                   mode="partitioned", partitions=detail, notes=notes)


def build_merge_input(key: str, partial: list[dict], uniques: list[dict], product: str,
                      prior_titles: list[str]) -> str:
    """The user message for the merge call: every partial cluster with all its tags."""
    lines = [
        f"PRODUCT CATEGORY: {product}",
        f"LIST: {key}",
    ]
    if prior_titles:
        lines += [
            "",
            "PRIOR CLUSTER TITLES (reuse a title exactly when the meaning matches; "
            "never join clusters to fit one, and never emit a title with no clusters)",
            *(f"- {t}" for t in prior_titles),
        ]
    lines += [
        "",
        f"CLUSTERS ({len(partial)}) — every index below must appear in exactly one cluster",
    ]
    for i, cluster in enumerate(partial):
        head = cluster["title"]
        if cluster.get("description"):
            head += f" — {cluster['description']}"
        tags = [uniques[j]["display"] for j in cluster["indices"]]
        lines.append(f"{i}. {head}")
        lines.append(f"   Tags ({len(tags)}): {', '.join(tags)}")
    return "\n".join(lines)


def _merge(key: str, partial: list[dict], uniques: list[dict], product: str,
           choice: dict, prior_titles: list[str]):
    """Join partial clusters that are one customer concern.

    Returns (clusters, repairs, usage, error); `clusters` is None on failure. The
    answer reuses SCHEMA with cluster indices in place of tag indices, so the same
    validate() enforces "every partial cluster exactly once". A cluster the model
    leaves alone keeps its title and description untouched.
    """
    prompt, _ = load_merge_prompt()
    merge_choice = {"model": choice["model"], "reasoning": config.GROUP_MERGE_REASONING}
    user_input = build_merge_input(key, partial, uniques, product, prior_titles)
    raw, usage, last = call_with_retries(
        prompt, user_input, merge_choice, schema_name="cluster_merge",
        max_output_tokens=config.GROUP_MERGE_MAX_OUTPUT_TOKENS,
        max_tokens_setting="GROUP_MERGE_MAX_OUTPUT_TOKENS",
    )
    if raw is None:
        return None, [], usage, last

    groups, repairs = validate(raw, [{"display": c["title"]} for c in partial])
    for repair in repairs:
        repair["stage"] = "merge"

    merged = []
    for group in groups:
        members = sorted((partial[i] for i in group["indices"]),
                         key=lambda c: -len(c["indices"]))
        indices = [i for m in members for i in m["indices"]]
        if len(members) == 1:
            title, description = members[0]["title"], members[0]["description"]
        else:
            title = group["title"] or members[0]["title"]
            description = group["description"] or members[0]["description"]
        merged.append({"title": title, "description": description,
                       "indices": indices, "members": len(members)})
    return merged, repairs, usage, ""


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def load_tagged(slug: str, run_id: str, parse_id: str) -> dict:
    directory = storage.run_dir(slug, run_id) / parse_id
    if storage.is_hidden(parse_id) or not directory.is_dir():
        raise GroupingError("That parse no longer exists.")
    tagged = storage.read_json(directory / "tagged.json")
    if not tagged:
        raise GroupingError("That parse didn't finish — there is nothing to group.")
    return tagged


def expected_output_tokens(tag_count: int) -> int:
    """Output tokens (reasoning included) one list of this size usually needs."""
    return config.GROUP_EST_OUTPUT_BASE + config.GROUP_EST_OUTPUT_PER_TAG * tag_count


def expected_seconds(output_tokens: int) -> int:
    return max(1, round(output_tokens / max(1, config.GROUP_TOKENS_PER_SEC)))


def which_lists(only: list[str] | None) -> tuple[str, ...]:
    """`only` validated against LISTS, in declared order; None means every list."""
    if only is None:
        return LISTS
    bad = [k for k in only if k not in LISTS]
    if bad:
        raise GroupingError(f"{', '.join(bad)} isn't a tag list this app groups.")
    return tuple(k for k in LISTS if k in only)


def estimate(tagged: dict, prompt: str | None = None, only: list[str] | None = None) -> dict:
    """What a grouping run will cost, for the confirm dialog. Spends nothing.

    `only` narrows it to the lists a partial re-run would group.
    """
    if prompt is None:
        prompt, _ = load_prompt()

    model = settings.resolve("group_model")
    prompt_tokens = len(prompt) // 4
    merge_prompt_tokens = None
    lists, input_tokens, output_tokens, embed_tokens = [], 0, 0, 0

    for key in which_lists(only):
        occurrences, uniques = collect(tagged, key)
        if not uniques:
            continue
        count = len(uniques)
        tag_tokens = sum(len(u["display"]) + 6 for u in uniques) // 4
        entry = {
            "key": key,
            "title": TITLES[key],
            "occurrences": len(occurrences),
            "unique_tags": count,
        }

        if count <= config.GROUP_SINGLE_CALL_MAX:
            list_input = prompt_tokens + tag_tokens + 60
            list_output = expected_output_tokens(count)
            entry.update(mode="single", partitions=1, seconds=expected_seconds(list_output))
        else:
            # Partitions run in parallel, then one merge call sees every tag again.
            if merge_prompt_tokens is None:
                merge_prompt_tokens = len(load_merge_prompt()[0]) // 4
            sizes = [len(p) for p in partition.slices(count, config.GROUP_PARTITION_TARGET)]
            part_outputs = [expected_output_tokens(s) for s in sizes]
            clusters_guess = max(1, count // 8)      # ~1 cluster per 8 tags on disk
            merge_output = (config.GROUP_MERGE_EST_OUTPUT_BASE
                            + config.GROUP_MERGE_EST_OUTPUT_PER_CLUSTER * clusters_guess)
            list_input = (len(sizes) * (prompt_tokens + 60) + tag_tokens
                          + merge_prompt_tokens + tag_tokens + clusters_guess * 20)
            list_output = sum(part_outputs) + merge_output
            embed_tokens += tag_tokens
            entry.update(mode="partitioned", partitions=len(sizes),
                         seconds=expected_seconds(max(part_outputs))
                                 + expected_seconds(merge_output))

        input_tokens += list_input
        output_tokens += list_output
        entry["output_tokens"] = list_output
        lists.append(entry)

    price = tagging._price(input_tokens, output_tokens, model)
    embed_rates = config.MODEL_PRICING.get(config.GROUP_EMBED_MODEL)
    if embed_tokens and price["cost_known"] and embed_rates:
        price["cost_usd"] = round(price["cost_usd"] + embed_tokens / 1e6 * embed_rates["input"], 4)

    return {
        "lists": lists,
        "to_group": len(lists),
        "model": model,
        "reasoning": settings.resolve("group_reasoning"),
        "single_call_max": config.GROUP_SINGLE_CALL_MAX,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "embedding_tokens": embed_tokens,
        **price,
    }


def prior_titles(slug: str, filename: str = "clusters.json",
                 item_key: str = "clusters") -> dict:
    """Cluster titles from the last time this category was grouped, whichever
    parse that was — including this one, if it has been grouped before.

    Cross-run comparability for the price of a few hundred prompt tokens: if the
    same concept shows up again, it keeps the name it had last time. Re-grouping
    the same parse counts, so running it twice doesn't rename stable clusters.

    `filename` / `item_key` let themes.py ask the same question of themes.json.
    """
    found: dict[str, list[str]] = {}
    newest = None

    group_root = storage.group_dir(slug)
    if not group_root.is_dir():
        return found

    for run_folder in group_root.iterdir():
        if not run_folder.is_dir() or storage.is_hidden(run_folder.name):
            continue
        for parse_folder in run_folder.iterdir():
            if not parse_folder.is_dir() or not parse_folder.name.startswith("parse-"):
                continue
            path = parse_folder / filename
            if not path.is_file():
                continue
            stamp = path.stat().st_mtime
            if newest is None or stamp > newest[0]:
                newest = (stamp, path)

    if newest is None:
        return found

    previous = storage.read_json(newest[1], {}) or {}
    for section in previous.get("sections") or []:
        titles = [c.get("title") for c in section.get(item_key) or [] if c.get("title")]
        if titles:
            found[section.get("key")] = titles
    return found


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def about(seconds: int) -> str:
    """'about 40 s' / 'about 4 min' — for a progress label, never a promise."""
    if seconds < 90:
        return f"about {max(10, (seconds // 10) * 10)} s"
    return f"about {round(seconds / 60)} min"


def run_grouping(job, slug: str, run_id: str, parse_id: str,
                 only: list[str] | None = None) -> dict:
    """Cluster every non-empty tag list in one parse and write clusters.json.

    Mirrors tagging.run_tagging: report progress through `job`, keep going past a
    single list's failure, and return the summary the UI shows.

    With `only`, this is a partial re-run: just those lists are grouped again and
    spliced into the existing clusters.json. The other lists — and the themes that
    were built on them — are kept exactly as they were, so a list that failed can
    be redone without paying for, or perturbing, the ones that succeeded.
    """
    started = time.perf_counter()
    prompt, prompt_sha = load_prompt()
    choice = group_choice()
    wanted = which_lists(only)
    partial = only is not None

    tagged = load_tagged(slug, run_id, parse_id)
    group = storage.get_group(slug)
    product = group.get("name") or slug.replace("-", " ").title()
    directory = storage.run_dir(slug, run_id) / parse_id

    previous = storage.read_json(directory / "clusters.json") if partial else None
    if partial and not previous:
        raise GroupingError(
            "This parse has never been grouped, so there is no list to re-run. "
            "Group the whole parse first."
        )

    collected = {}
    for key in wanted:
        occurrences, uniques = collect(tagged, key)
        if uniques:
            collected[key] = (occurrences, uniques)

    if not collected:
        if partial:
            names = ", ".join(TITLES[k].lower() for k in wanted)
            raise GroupingError(f"This parse has no {names} tags, so there is nothing to re-run.")
        raise GroupingError(
            "This parse produced no features, complaints or care instructions, so "
            "there is nothing to group. Parse again, or add more reviews first."
        )

    priors = prior_titles(slug)
    if priors:
        job.note(
            "Reusing cluster titles from the previous grouping of this category "
            "where the themes match, so the two runs can be compared."
        )

    # A big list sits still for minutes. Say how long it usually takes, so a
    # motionless bar reads as "working" rather than "hung". A partitioned list
    # gets one unit of progress per partition plus one for the merge.
    plan = {l["key"]: l for l in estimate(tagged, prompt, only=list(collected))["lists"]}
    units = {key: (l["partitions"] + 1 if l["mode"] == "partitioned" else 1)
             for key, l in plan.items()}
    biggest = max(plan.values(), key=lambda l: l["unique_tags"])
    eta = about(max(l["seconds"] for l in plan.values()))
    # The estimate guesses the partition count from the size; the real count only
    # exists once the tags are embedded, so the label doesn't name one.
    shape = (f"{biggest['unique_tags']} tags, partitioned"
             if biggest["mode"] == "partitioned" else f"{biggest['unique_tags']} tags")
    verb = "Re-grouping" if partial else "Grouping"
    total_units = sum(units.values())
    job.set_total(total_units)
    job.step(label=f"{verb} {len(collected)} tag {'list' if len(collected) == 1 else 'lists'} "
                   f"with {choice['model']}… (largest has {shape}, {eta})", count=0)

    fresh: list[dict] = []
    lock = threading.Lock()
    fatal: list[str] = []
    stepped: dict[str, int] = {key: 0 for key in collected}

    def progress_for(key: str):
        # Partitions step the bar as they finish; the last unit is the list itself.
        def advance(label: str) -> None:
            with lock:
                if stepped[key] < units[key] - 1:
                    stepped[key] += 1
                    job.step(label=label, count=1)
                else:
                    job.step(label=label, count=0)
        return advance

    def work(key: str) -> None:
        if fatal:
            return
        occurrences, uniques = collected[key]
        try:
            section = group_list(key, uniques, occurrences, product, prompt, choice,
                                 priors.get(key) or [], progress=progress_for(key))
        except GroupingError as exc:
            with lock:
                if not fatal:
                    fatal.append(str(exc))
            return

        section["occurrences"] = occurrences
        section["unique_tags"] = uniques
        # Each section says what produced it, so a document assembled from two
        # runs (a partial re-run on a different model, say) stays self-describing.
        section["model"] = choice["model"]
        section["reasoning"] = choice["reasoning"]
        section["prompt_sha"] = prompt_sha
        section["grouped_at"] = storage.now_iso()
        with lock:
            fresh.append(section)
            for note in section.get("notes") or []:
                job.note(note)
            if section["status"] == "failed":
                job.fail_item(TITLES[key], section["error"])
            job.step(label=f"Grouped {len(fresh)} of {len(collected)} lists",
                     count=units[key] - stepped[key])
            stepped[key] = units[key]

    keys = list(collected)
    with ThreadPoolExecutor(max_workers=max(1, min(config.OPENAI_WORKERS, len(keys)))) as pool:
        list(pool.map(work, keys))

    if fatal:
        raise GroupingError(fatal[0])

    # Splice a partial re-run into what was there; the untouched sections come
    # through byte for byte. Then restore the declared list order — the pool
    # returns sections as they finish.
    sections = list(fresh)
    if partial:
        sections += [s for s in previous.get("sections") or [] if s.get("key") not in collected]
    sections.sort(key=lambda s: LISTS.index(s["key"]))

    document = {
        "parse_id": parse_id,
        "run_id": run_id,
        "slug": slug,
        "product": product,
        "generated_at": storage.now_iso(),
        "model": choice["model"],
        "reasoning": choice["reasoning"],
        "prompt_sha": prompt_sha,
        "sections": sections,
    }
    storage.write_json(directory / "clusters.json", document)
    (directory / "clusters.md").write_text(render_markdown(document), encoding="utf-8")

    # Themes describe one exact set of clusters. New clusters make any old themes
    # wrong, so they go before anything else can read them; results.py also checks
    # a fingerprint, so a leftover file could never be shown anyway. On a partial
    # re-run the themes of the untouched lists are still right, so they are lifted
    # out first (only if they matched the old clusters) and written back under the
    # new fingerprint by run_themes.
    from pipeline import themes   # here, not at the top: themes imports this module
    carried: list[dict] = []
    if partial:
        old_themes = themes.read_themes(directory, previous)
        if old_themes:
            carried = [s for s in old_themes.get("sections") or []
                       if s.get("key") not in collected]
    themes.remove_files(directory)

    theme_outcome = {"status": "skipped", "lists": [], "error": None}
    auto = bool(settings.resolve("auto_themes"))
    if auto or carried:
        job.set_total(total_units + 1)
        job.step(label="Grouping clusters into themes…" if auto
                 else "Keeping the existing themes…", count=0)
        try:
            theme_summary = themes.run_themes(
                job, slug, run_id, parse_id, clusters_doc=document, standalone=False,
                only=(list(collected) if partial else None) if auto else [],
                carry=carried,
            )
            theme_outcome = {
                "status": "failed" if theme_summary["failed"] else ("ok" if auto else "skipped"),
                "lists": theme_summary["lists"],
                "error": None,
            }
        except themes.ThemeError as exc:
            # Clusters are done and saved. A theme problem is a note, not a failure.
            theme_outcome = {"status": "failed", "lists": [], "error": str(exc)}
            job.note(f"Themes could not be built: {exc} Use \"Group into themes\" on "
                     f"the results view to try again.")
        job.step(count=1)

    # Spend and repairs are this run's; statuses and failures cover the whole
    # document, since that is what the results view will show.
    usage = {
        "input_tokens": sum(s["usage"]["input_tokens"] for s in fresh),
        "output_tokens": sum(s["usage"]["output_tokens"] for s in fresh),
    }
    actual = tagging._price(usage["input_tokens"], usage["output_tokens"], choice["model"])
    repairs = sum(len(s["repairs"]) for s in fresh)
    failures = [{"list": s["title"], "reason": s["error"]}
                for s in sections if s["status"] == "failed"]

    if repairs:
        job.note(
            f"{repairs} tag {'placement' if repairs == 1 else 'placements'} had to be "
            f"repaired — every one is listed in cluster_log.json. No tag was dropped."
        )
    if failures:
        job.note(f"{len(failures)} of {len(sections)} lists could not be grouped.")
    if partial:
        kept = [TITLES[s["key"]].lower() for s in sections if s["key"] not in collected]
        if kept:
            job.note(f"Kept {' and '.join(kept)} as they were.")

    summary = {
        "parse_id": parse_id,
        "run_id": run_id,
        "slug": slug,
        "lists": [
            {
                "key": s["key"],
                "title": s["title"],
                "status": s["status"],
                "occurrences": len(s["occurrences"]),
                "unique_tags": len(s["unique_tags"]),
                "clusters": len(s["clusters"]),
            }
            for s in sections
        ],
        "clusters_total": sum(len(s["clusters"]) for s in sections),
        "repairs": repairs,
        "failed": len(failures),
        "usage": usage,
        "cost_usd": actual["cost_usd"],
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "rerun_lists": list(collected) if partial else [],
        "themes": theme_outcome,
    }

    storage.write_json(directory / "cluster_log.json", {
        **summary,
        "generated_at": storage.now_iso(),
        "model": choice["model"],
        "reasoning": choice["reasoning"],
        "prompt_sha": prompt_sha,
        "prior_titles_used": {k: len(v) for k, v in priors.items()},
        "repair_detail": {s["key"]: s["repairs"] for s in sections if s.get("repairs")},
        "partition_detail": {s["key"]: s["partitions"] for s in sections if s.get("partitions")},
        "modes": {s["key"]: s.get("mode", "single") for s in sections},
        "failures": failures,
        "notes": list(job.notes),
    })

    return summary


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def render_markdown(document: dict) -> str:
    """The same shape as the hand-made files in tag-clustering-examples/.

    Rendered from clusters.json in code rather than asked of the model, so the two
    artifacts cannot disagree about what is in a cluster.
    """
    out = [f"# Tag Clusters: {document.get('product') or 'Product'}", ""]

    for section in document.get("sections") or []:
        out.append(f"## {section['title']}")
        out.append("")
        if section.get("status") == "failed":
            out.append(f"*This list could not be grouped: {section.get('error')}*")
            out.append("")
            continue

        by_uid = {u["uid"]: u for u in section.get("unique_tags") or []}
        for cluster in section.get("clusters") or []:
            tags = [by_uid[uid]["display"] for uid in cluster["uids"] if uid in by_uid]
            out.append(f"### {cluster['number']}. {cluster['title']}")
            if cluster.get("description"):
                out.append(cluster["description"])
            out.append("")
            out.append(f"**Tags ({len(tags)}):** {', '.join(tags)}")
            out.append("")

    return "\n".join(out).rstrip() + "\n"
