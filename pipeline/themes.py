"""
Step 3b — grouping clusters into themes, a "group of groups".

Clustering (grouping.py) turns a few hundred tags into 20–60 clusters per list, which
is still too many to read as a picture. This step makes one more OpenAI call per list
and folds those clusters into a handful of themes, each a customer need with a title
and a one-sentence summary. The rules live in prompts/group_themes.md; this module is
the machinery, and it mirrors grouping.py deliberately: the same call, the same
"every index exactly once" validation, the same repair log.

Two things are specific to this level:

- **Only long lists are themed.** A list with fewer than THEME_MIN_CLUSTERS clusters
  reads fine as a flat grid, so it is left alone and the UI shows it as before.
- **Themes are bound to one exact set of clusters.** themes.json carries a fingerprint
  of the clusters it was built from; results.py refuses to show themes whose
  fingerprint no longer matches, and run_grouping deletes the theme files outright
  before it writes new clusters. An old parse that was never themed just has no file.

Terminology, here and in the UI: a *cluster* is the tag cluster from grouping.py; a
*theme* is a group of clusters. "Group" on its own already means a competitor
category in this codebase, so it is never used for either.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from pipeline import grouping, storage, tagging

PROMPT_PATH = Path(__file__).parent / "prompts" / "group_themes.md"

# Everything the theme step writes into a parse folder. run_grouping removes all of
# them before it writes new clusters.
FILES = ("themes.json", "themes.md", "themes_log.json")

SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "indices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "summary", "indices"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["themes"],
    "additionalProperties": False,
}


class ThemeError(Exception):
    """A problem that stops the whole theme run. Shown to the user verbatim."""


# ---------------------------------------------------------------------------
# Prompt, eligibility, fingerprint, files
# ---------------------------------------------------------------------------

def load_prompt() -> tuple[str, str]:
    """The instruction text and a hash of it, for themes_log.json."""
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ThemeError(
            f"The theme instructions are missing. Expected them at {PROMPT_PATH}."
        ) from None
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def eligible(section: dict) -> bool:
    """Whether a clusters.json section has enough clusters to be worth theming."""
    return (section.get("status") == "ok"
            and len(section.get("clusters") or []) >= config.THEME_MIN_CLUSTERS)


def fingerprint(clusters_doc: dict) -> str:
    """A short hash of exactly which clusters exist and what they hold.

    Ids, titles and member uids — everything a theme refers to. If any of it
    changes, themes built against the old set are meaningless and must not show.
    """
    shape = [
        [s.get("key"), [[c.get("cluster_id"), c.get("title"), list(c.get("uids") or [])]
                        for c in s.get("clusters") or []]]
        for s in clusters_doc.get("sections") or []
    ]
    raw = json.dumps(shape, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def remove_files(directory: Path) -> None:
    for name in FILES:
        (Path(directory) / name).unlink(missing_ok=True)


def load_clusters(slug: str, run_id: str, parse_id: str) -> dict:
    directory = storage.run_dir(slug, run_id) / parse_id
    if storage.is_hidden(parse_id) or not directory.is_dir():
        raise ThemeError("That parse no longer exists.")
    clusters = storage.read_json(directory / "clusters.json")
    if not clusters:
        raise ThemeError("This parse hasn't been grouped into clusters yet. Group tags first.")
    return clusters


def read_themes(directory: Path, clusters_doc: dict) -> dict | None:
    """themes.json if it exists and still describes these clusters, else None."""
    doc = storage.read_json(Path(directory) / "themes.json")
    if not doc or doc.get("clusters_fingerprint") != fingerprint(clusters_doc):
        return None
    return doc


# ---------------------------------------------------------------------------
# One call
# ---------------------------------------------------------------------------

def build_input(key: str, section: dict, product: str, prior_titles: list[str]) -> str:
    """The user message for one list: every cluster with its description and tags."""
    by_uid = {u["uid"]: u for u in section.get("unique_tags") or []}
    clusters = section.get("clusters") or []

    lines = [
        f"PRODUCT CATEGORY: {product}",
        f"LIST: {key}",
    ]
    if prior_titles:
        lines += [
            "",
            "PRIOR THEME TITLES (reuse a title exactly when the meaning matches; "
            "never force a cluster into one, and never emit a title with no clusters)",
            *(f"- {title}" for title in prior_titles),
        ]
    lines += [
        "",
        f"CLUSTERS ({len(clusters)}) — every index below must appear in exactly one theme",
    ]
    for i, cluster in enumerate(clusters):
        tags = [by_uid[uid]["display"] for uid in cluster.get("uids") or [] if uid in by_uid]
        head = cluster.get("title") or ""
        if cluster.get("description"):
            head += f" — {cluster['description']}"
        lines.append(f"{i}. {head}")
        lines.append(f"   Tags ({len(tags)}): {', '.join(tags)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(raw_themes: list[dict], clusters: list[dict]) -> tuple[list[dict], list[dict]]:
    """Force the answer back onto the invariant: every cluster in exactly one theme.

    Same contract as grouping.validate. A cluster the model forgot becomes its own
    theme, wearing the cluster's title and description, and every repair is logged.
    """
    total = len(clusters)
    seen: set[int] = set()
    themes: list[dict] = []
    repairs: list[dict] = []

    for theme in raw_themes:
        if not isinstance(theme, dict):
            continue
        title = (theme.get("title") or "").strip()
        summary = (theme.get("summary") or "").strip()

        kept: list[int] = []
        for index in theme.get("indices") or []:
            if not isinstance(index, int) or not 0 <= index < total:
                repairs.append({"kind": "out_of_range", "index": index, "theme": title})
                continue
            if index in seen:
                repairs.append({
                    "kind": "duplicate",
                    "cluster": clusters[index].get("title"),
                    "theme": title,
                    "note": "kept the first placement",
                })
                continue
            seen.add(index)
            kept.append(index)

        if not kept:
            continue                    # an empty theme is not a theme
        themes.append({
            "title": title or clusters[kept[0]].get("title") or "Untitled",
            "summary": summary,
            "indices": kept,
        })

    for index in range(total):
        if index in seen:
            continue
        cluster = clusters[index]
        repairs.append({"kind": "orphan", "cluster": cluster.get("title"),
                        "note": "made a one-cluster theme"})
        themes.append({
            "title": cluster.get("title") or "Untitled",
            "summary": cluster.get("description") or "",
            "indices": [index],
        })

    return themes, repairs


# ---------------------------------------------------------------------------
# One list, end to end
# ---------------------------------------------------------------------------

def theme_list(section: dict, product: str, prompt: str, choice: dict,
               prior_titles: list[str]) -> dict:
    """Theme one clusters.json section. Never raises for a bad answer — that is a
    failed list; only fatal API problems come out as ThemeError."""
    key = section["key"]
    clusters = section.get("clusters") or []
    base = {"key": key, "title": section.get("title") or grouping.TITLES.get(key, key)}

    user_input = build_input(key, section, product, prior_titles)
    raw, usage, last = grouping.call_with_retries(
        prompt, user_input, choice, what="themed", error_cls=ThemeError,
        schema=SCHEMA, schema_name="cluster_themes", result_key="themes",
        max_output_tokens=config.THEME_MAX_OUTPUT_TOKENS,
        max_tokens_setting="THEME_MAX_OUTPUT_TOKENS",
    )
    if raw is None:
        return {**base, "status": "failed", "error": last,
                "themes": [], "repairs": [], "usage": usage}

    themes, repairs = validate(raw, clusters)

    by_uid = {u["uid"]: u for u in section.get("unique_tags") or []}
    by_occ = {o["occ_id"]: o for o in section.get("occurrences") or []}
    built = []
    for theme in themes:
        members = sorted((clusters[i] for i in theme["indices"]),
                         key=lambda c: (-c.get("total_tags", 0), c.get("number", 0)))
        uids = [uid for c in members for uid in c.get("uids") or []]
        built.append({
            "title": theme["title"],
            "summary": theme["summary"],
            "cluster_ids": [c["cluster_id"] for c in members],
            "clusters": len(members),
            "distinct_tags": len(uids),
            **grouping._counts(uids, by_uid, by_occ),
        })

    # Largest first by mentions, the same ranking the clusters themselves use.
    built.sort(key=lambda t: (-t["total_tags"], t["title"].lower()))
    for number, theme in enumerate(built, 1):
        theme["theme_id"] = f"t_{number:02d}"
        theme["number"] = number

    return {**base, "status": "ok", "error": None,
            "themes": built, "repairs": repairs, "usage": usage}


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def estimate(clusters_doc: dict, prompt: str | None = None) -> dict:
    """What theming this parse will cost, for the confirm dialog. Spends nothing."""
    if prompt is None:
        prompt, _ = load_prompt()

    choice = grouping.group_choice()
    prompt_tokens = len(prompt) // 4
    lists, skipped, input_tokens, output_tokens = [], [], 0, 0

    for section in clusters_doc.get("sections") or []:
        clusters = section.get("clusters") or []
        entry = {
            "key": section.get("key"),
            "title": section.get("title"),
            "clusters": len(clusters),
            "status": section.get("status"),
        }
        if not eligible(section):
            skipped.append(entry)
            continue
        by_uid = {u["uid"]: u for u in section.get("unique_tags") or []}
        chars = 0
        for c in clusters:
            chars += len(c.get("title") or "") + len(c.get("description") or "") + 20
            chars += sum(len(by_uid[u]["display"]) + 2 for u in c.get("uids") or [] if u in by_uid)
        input_tokens += prompt_tokens + chars // 4 + 60
        output_tokens += config.THEME_EST_OUTPUT_TOKENS
        lists.append(entry)

    return {
        "lists": lists,
        "skipped": skipped,
        "to_theme": len(lists),
        "min_clusters": config.THEME_MIN_CLUSTERS,
        "model": choice["model"],
        "reasoning": choice["reasoning"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        **tagging._price(input_tokens, output_tokens, choice["model"]),
    }


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run_themes(job, slug: str, run_id: str, parse_id: str,
               clusters_doc: dict | None = None, standalone: bool = True) -> dict:
    """Theme every eligible list in one parse and write themes.json.

    `standalone` is True when this is its own job (the "Group into themes" button)
    and owns the progress bar. When run_grouping calls it, the clustering job owns
    the bar and this step only updates the label.
    """
    started = time.perf_counter()
    prompt, prompt_sha = load_prompt()
    choice = grouping.group_choice()

    if clusters_doc is None:
        clusters_doc = load_clusters(slug, run_id, parse_id)
    directory = storage.run_dir(slug, run_id) / parse_id
    product = clusters_doc.get("product") or slug.replace("-", " ").title()

    todo = [s for s in clusters_doc.get("sections") or [] if eligible(s)]
    priors = grouping.prior_titles(slug, "themes.json", "themes")
    if todo and priors:
        job.note(
            "Reusing theme titles from the previous theming of this category where "
            "the meaning matches, so the two runs can be compared."
        )

    def progress(label: str, count: int) -> None:
        job.step(label=label, count=count if standalone else 0)

    if standalone:
        job.set_total(len(todo))
    if todo:
        progress(f"Grouping {len(todo)} {'list' if len(todo) == 1 else 'lists'} of "
                 f"clusters into themes with {choice['model']}…", 0)

    sections: list[dict] = []
    lock = threading.Lock()
    fatal: list[str] = []

    def work(section: dict) -> None:
        if fatal:
            return
        try:
            result = theme_list(section, product, prompt, choice,
                                priors.get(section["key"]) or [])
        except ThemeError as exc:
            with lock:
                if not fatal:
                    fatal.append(str(exc))
            return
        with lock:
            sections.append(result)
            if result["status"] == "failed":
                job.fail_item(result["title"], result["error"])
            progress(f"Themed {len(sections)} of {len(todo)} lists", 1)

    if todo:
        workers = max(1, min(config.OPENAI_WORKERS, len(todo)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, todo))

    if fatal:
        raise ThemeError(fatal[0])

    sections.sort(key=lambda s: grouping.LISTS.index(s["key"]))

    document = {
        "parse_id": parse_id,
        "run_id": run_id,
        "slug": slug,
        "product": product,
        "generated_at": storage.now_iso(),
        "model": choice["model"],
        "reasoning": choice["reasoning"],
        "prompt_sha": prompt_sha,
        "min_clusters": config.THEME_MIN_CLUSTERS,
        "clusters_fingerprint": fingerprint(clusters_doc),
        "clusters_generated_at": clusters_doc.get("generated_at"),
        "sections": sections,
    }
    storage.write_json(directory / "themes.json", document)
    (directory / "themes.md").write_text(render_markdown(document, clusters_doc),
                                         encoding="utf-8")

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
            f"{repairs} cluster {'placement' if repairs == 1 else 'placements'} had to "
            f"be repaired while building themes — every one is listed in "
            f"themes_log.json. No cluster was dropped."
        )
    if failures:
        job.note(f"{len(failures)} of {len(todo)} lists could not be grouped into themes.")
    if not todo:
        job.note(
            f"No list has {config.THEME_MIN_CLUSTERS} or more clusters, so nothing was "
            f"grouped into themes."
        )

    summary = {
        "parse_id": parse_id,
        "run_id": run_id,
        "slug": slug,
        "lists": [
            {
                "key": s["key"],
                "title": s["title"],
                "status": s["status"],
                "clusters": sum(t["clusters"] for t in s["themes"]),
                "themes": len(s["themes"]),
            }
            for s in sections
        ],
        "themes_total": sum(len(s["themes"]) for s in sections),
        "repairs": repairs,
        "failed": len(failures),
        "usage": usage,
        "cost_usd": actual["cost_usd"],
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }

    storage.write_json(directory / "themes_log.json", {
        **summary,
        "generated_at": storage.now_iso(),
        "model": choice["model"],
        "reasoning": choice["reasoning"],
        "prompt_sha": prompt_sha,
        "clusters_fingerprint": document["clusters_fingerprint"],
        "prior_titles_used": {k: len(v) for k, v in priors.items()},
        "repair_detail": {s["key"]: s["repairs"] for s in sections if s["repairs"]},
        "failures": failures,
        "notes": list(job.notes),
    })

    return summary


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def render_markdown(themes_doc: dict, clusters_doc: dict) -> str:
    """The shape of the hand-made files in group-clustering-examples/, with
    "Clusters" where they say "Groups". Member counts are distinct tags, the same
    number clusters.md prints, so the skill's check_grouping.py can verify this
    file against clusters.md.
    """
    out = [f"# Cluster Themes: {themes_doc.get('product') or 'Product'}", ""]
    themed = {s["key"]: s for s in themes_doc.get("sections") or []}

    for section in clusters_doc.get("sections") or []:
        key = section.get("key")
        out.append(f"## {section.get('title')}")
        out.append("")

        result = themed.get(key)
        if result is None:
            n = len(section.get("clusters") or [])
            out.append(f"*Not themed: {n} {'cluster' if n == 1 else 'clusters'} "
                       f"(themes need {themes_doc.get('min_clusters', config.THEME_MIN_CLUSTERS)} or more).*")
            out.append("")
            continue
        if result.get("status") == "failed":
            out.append(f"*This list could not be grouped into themes: {result.get('error')}*")
            out.append("")
            continue

        by_id = {c["cluster_id"]: c for c in section.get("clusters") or []}
        for theme in result.get("themes") or []:
            out.append(f"### {theme['number']}. {theme['title']}")
            if theme.get("summary"):
                out.append(theme["summary"])
            out.append("")
            out.append(f"**Clusters ({theme['clusters']}) · Tags ({theme['distinct_tags']}) "
                       f"· Mentions ({theme['total_tags']}):**")
            for cluster_id in theme.get("cluster_ids") or []:
                cluster = by_id.get(cluster_id)
                if cluster:
                    out.append(f"- {cluster['title']} ({len(cluster.get('uids') or [])})")
            out.append("")

    return "\n".join(out).rstrip() + "\n"
