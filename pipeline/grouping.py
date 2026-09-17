"""
Step 3 — turning thousands of loose tags into a few readable product concepts.

One OpenAI call per tag list, which is the whole design. Clustering is a global
judgement: deciding that "sturdy bowl" and "thick build" are one customer concern
means comparing them against every other tag at once. Chunking the list would make
that comparison impossible for any pair that landed in different chunks, and no
merge pass can repair it afterwards because the merge only ever sees titles. The
measurements say we never have to: the largest list on disk is ~2,264 tokens.

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
from pipeline import settings, storage, tagging

PROMPT_PATH = Path(__file__).parent / "prompts" / "group_tags.md"

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

def load_prompt() -> tuple[str, str]:
    """The instruction text and a hash of it, for cluster_log.json."""
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GroupingError(
            f"The grouping instructions are missing. Expected them at {PROMPT_PATH}."
        ) from None
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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
          error_cls: type[Exception] = GroupingError) -> tuple[list[dict], dict]:
    """One request. Returns (items, usage) or raises.

    Parametrised because the theme step (themes.py) makes the same kind of call
    with a different schema and a different error class; the defaults keep this
    module's own behaviour unchanged.
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
            raise error_cls(
                f"The answer was cut off at {max_output_tokens} tokens. "
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
               prompt: str, choice: dict, prior_titles: list[str]) -> dict:
    """Cluster one tag list, retrying the errors worth retrying."""
    if len(uniques) > config.GROUP_CHUNK_SIZE:
        # Deliberately an error rather than a silent chunking fallback. Splitting
        # the list would quietly produce worse clusters, and a wrong answer that
        # looks fine is the one failure mode worth refusing outright.
        return {
            "key": key, "title": TITLES[key], "status": "failed",
            "error": (
                f"{len(uniques)} unique tags is past the {config.GROUP_CHUNK_SIZE} "
                f"this step will cluster in one call. Raise GROUP_CHUNK_SIZE in "
                f"config.py, or narrow the run to fewer ASINs."
            ),
            "clusters": [], "repairs": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    user_input = build_input(key, uniques, product, prior_titles)
    raw, usage, last = call_with_retries(prompt, user_input, choice)

    if raw is None:
        return {"key": key, "title": TITLES[key], "status": "failed", "error": last,
                "clusters": [], "repairs": [], "usage": usage}

    clusters, repairs = validate(raw, uniques)

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

    # Largest first, so the biggest customer concern is the first thing read. Ties
    # break on title for a stable file that diffs cleanly between two runs.
    built.sort(key=lambda c: (-c["total_tags"], c["title"].lower()))
    for number, cluster in enumerate(built, 1):
        cluster["cluster_id"] = f"g_{number:02d}"
        cluster["number"] = number

    return {"key": key, "title": TITLES[key], "status": "ok", "error": None,
            "clusters": built, "repairs": repairs, "usage": usage}


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


def estimate(tagged: dict, prompt: str | None = None) -> dict:
    """What a grouping run will cost, for the confirm dialog. Spends nothing."""
    if prompt is None:
        prompt, _ = load_prompt()

    model = settings.resolve("group_model")
    prompt_tokens = len(prompt) // 4
    lists, input_tokens, output_tokens = [], 0, 0

    for key in LISTS:
        occurrences, uniques = collect(tagged, key)
        if not uniques:
            continue
        tag_chars = sum(len(u["display"]) + 6 for u in uniques)
        input_tokens += prompt_tokens + tag_chars // 4 + 60
        output_tokens += config.GROUP_EST_OUTPUT_TOKENS
        lists.append({
            "key": key,
            "title": TITLES[key],
            "occurrences": len(occurrences),
            "unique_tags": len(uniques),
        })

    return {
        "lists": lists,
        "to_group": len(lists),
        "model": model,
        "reasoning": settings.resolve("group_reasoning"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        **tagging._price(input_tokens, output_tokens, model),
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

def run_grouping(job, slug: str, run_id: str, parse_id: str) -> dict:
    """Cluster every non-empty tag list in one parse and write clusters.json.

    Mirrors tagging.run_tagging: report progress through `job`, keep going past a
    single list's failure, and return the summary the UI shows.
    """
    started = time.perf_counter()
    prompt, prompt_sha = load_prompt()
    choice = group_choice()

    tagged = load_tagged(slug, run_id, parse_id)
    group = storage.get_group(slug)
    product = group.get("name") or slug.replace("-", " ").title()

    collected = {}
    for key in LISTS:
        occurrences, uniques = collect(tagged, key)
        if uniques:
            collected[key] = (occurrences, uniques)

    if not collected:
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

    job.set_total(len(collected))
    job.step(label=f"Grouping {len(collected)} tag lists with {choice['model']}…", count=0)

    sections: list[dict] = []
    lock = threading.Lock()
    fatal: list[str] = []

    def work(key: str) -> None:
        if fatal:
            return
        occurrences, uniques = collected[key]
        try:
            section = group_list(key, uniques, occurrences, product, prompt, choice,
                                 priors.get(key) or [])
        except GroupingError as exc:
            with lock:
                if not fatal:
                    fatal.append(str(exc))
            return

        section["occurrences"] = occurrences
        section["unique_tags"] = uniques
        with lock:
            sections.append(section)
            if section["status"] == "failed":
                job.fail_item(TITLES[key], section["error"])
            job.step(label=f"Grouped {len(sections)} of {len(collected)} lists")

    keys = list(collected)
    with ThreadPoolExecutor(max_workers=max(1, min(config.OPENAI_WORKERS, len(keys)))) as pool:
        list(pool.map(work, keys))

    if fatal:
        raise GroupingError(fatal[0])

    # Restore the declared list order — the pool returns them as they finish.
    sections.sort(key=lambda s: LISTS.index(s["key"]))

    directory = storage.run_dir(slug, run_id) / parse_id
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
    # a fingerprint, so a leftover file could never be shown anyway.
    from pipeline import themes   # here, not at the top: themes imports this module
    themes.remove_files(directory)

    theme_outcome = {"status": "skipped", "lists": [], "error": None}
    if settings.resolve("auto_themes"):
        job.set_total(len(collected) + 1)
        job.step(label="Grouping clusters into themes…", count=0)
        try:
            theme_summary = themes.run_themes(job, slug, run_id, parse_id,
                                              clusters_doc=document, standalone=False)
            theme_outcome = {
                "status": "failed" if theme_summary["failed"] else "ok",
                "lists": theme_summary["lists"],
                "error": None,
            }
        except themes.ThemeError as exc:
            # Clusters are done and saved. A theme problem is a note, not a failure.
            theme_outcome = {"status": "failed", "lists": [], "error": str(exc)}
            job.note(f"Themes could not be built: {exc} Use \"Group into themes\" on "
                     f"the results view to try again.")
        job.step(count=1)

    usage = {
        "input_tokens": sum(s["usage"]["input_tokens"] for s in sections),
        "output_tokens": sum(s["usage"]["output_tokens"] for s in sections),
    }
    actual = tagging._price(usage["input_tokens"], usage["output_tokens"], choice["model"])
    repairs = sum(len(s["repairs"]) for s in sections)
    failures = [{"list": s["title"], "reason": s["error"]}
                for s in sections if s["status"] == "failed"]

    if repairs:
        job.note(
            f"{repairs} tag {'placement' if repairs == 1 else 'placements'} had to be "
            f"repaired — every one is listed in cluster_log.json. No tag was dropped."
        )
    if failures:
        job.note(f"{len(failures)} of {len(collected)} lists could not be grouped.")

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
        "themes": theme_outcome,
    }

    storage.write_json(directory / "cluster_log.json", {
        **summary,
        "generated_at": storage.now_iso(),
        "model": choice["model"],
        "reasoning": choice["reasoning"],
        "prompt_sha": prompt_sha,
        "prior_titles_used": {k: len(v) for k, v in priors.items()},
        "repair_detail": {s["key"]: s["repairs"] for s in sections if s["repairs"]},
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
