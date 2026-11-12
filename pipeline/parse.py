"""
Parse results: tagging, then clustering and themes, as one job.

The two steps used to be two buttons, which meant waiting for tagging to finish
only to press the second one. Tagging has not failed a single body in any parse
on disk, so they now run back to back behind one confirmation and one cost
figure, with a quick check in between:

  * more than PARSE_MAX_FAILED_SHARE of the bodies failed to tag, or
  * tagging found no features, complaints or care instructions at all

either of which stops before clustering. The tags are kept either way — the
parse folder is the same one tagging alone would have written — so nothing paid
for is lost, and "Group tags" on the run page clusters them without re-tagging.

Nothing about the steps themselves changes: this module only sequences
`tagging.run_tagging` and `grouping.run_grouping`, and prices them together.
"""

from __future__ import annotations

import config
from pipeline import grouping, jobs, settings, storage, tagging, themes

# Average tokens in one tag, for projecting clustering input before tags exist.
_TOKENS_PER_TAG = 6


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------------------
# Cost, before anything is spent
# ---------------------------------------------------------------------------

def _projected_sizes(to_tag: int) -> dict:
    """Tag-list sizes a parse of `to_tag` bodies usually produces."""
    sizes = {}
    for key, per_body in config.PARSE_EST_UNIQUE_TAGS_PER_BODY.items():
        count = round(to_tag * per_body)
        if count:
            sizes[key] = {"unique_tags": count, "occurrences": count,
                          "tag_tokens": count * _TOKENS_PER_TAG}
    return sizes


def _theme_estimate(sizes: dict) -> dict:
    """Theme cost for the projected lists long enough to be themed."""
    choice = themes.theme_choice()
    prompt_tokens = len(themes.load_prompt()[0]) // 4
    lists, input_tokens, output_tokens = 0, 0, 0
    if settings.resolve("auto_themes"):
        for key, size in sizes.items():
            clusters = size["unique_tags"] * config.PARSE_EST_CLUSTERS_PER_TAG.get(key, 0)
            if clusters < config.THEME_MIN_CLUSTERS:
                continue
            lists += 1
            # Every tag is shown again under its cluster, plus a title and a line each.
            input_tokens += prompt_tokens + size["tag_tokens"] + round(clusters) * 40 + 60
            output_tokens += config.THEME_EST_OUTPUT_TOKENS
    return {
        "lists": lists,
        "model": choice["model"],
        "reasoning": choice["reasoning"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "seconds": grouping.expected_seconds(config.THEME_EST_OUTPUT_TOKENS) if lists else 0,
        **tagging._price(input_tokens, output_tokens, choice["model"]),
    }


def estimate(bodies: list[dict]) -> dict:
    """What the whole parse will cost and how long it will take.

    Tagging is priced from the actual text. Clustering and themes are priced
    from projected tag counts, so that share is rougher — it is about a third of
    the total, and a category with unusually many or few tags moves it.
    """
    tag = tagging.estimate(bodies)
    sizes = _projected_sizes(tag["to_tag"])
    group = grouping.estimate_sizes(sizes) if sizes else None
    theme = _theme_estimate(sizes)

    parts = [tag, group, theme] if group else [tag]
    known = all(p["cost_known"] for p in parts)
    tag_seconds = round(tag["to_tag"] * config.TAG_SECONDS_PER_CALL
                        / max(1, min(config.OPENAI_WORKERS, tag["to_tag"] or 1)))
    group_seconds = (max((l["seconds"] for l in group["lists"]), default=0) if group else 0)

    return {
        "tagging": tag,
        "grouping": group,
        "themes": theme,
        "cost_known": known,
        "tagging_cost_usd": tag["cost_usd"],
        "grouping_cost_usd": (round(group["cost_usd"] + theme["cost_usd"], 4)
                              if group and known else None),
        "cost_usd": round(sum(p["cost_usd"] for p in parts), 4) if known else None,
        "tagging_seconds": tag_seconds,
        "grouping_seconds": group_seconds + theme["seconds"],
        "seconds": tag_seconds + group_seconds + theme["seconds"],
    }


# ---------------------------------------------------------------------------
# The check between the two steps
# ---------------------------------------------------------------------------

def check_tagging(summary: dict) -> str | None:
    """Why clustering shouldn't start on these tags, or None when it should."""
    tagged, failed = summary.get("tagged") or 0, summary.get("failed") or 0
    attempted = tagged + failed

    if attempted and failed / attempted > config.PARSE_MAX_FAILED_SHARE:
        return (f"{failed} of {_plural(attempted, 'body', 'bodies')} couldn't be tagged, "
                f"so clustering was not started. The tags that did land are saved — press "
                f"Parse results to try again, or Group tags to cluster what there is.")

    found = sum(summary.get(f"{key}_total") or 0 for key in grouping.LISTS)
    if not found:
        return ("Tagging found no features, complaints or care instructions in this run, "
                "so there is nothing to cluster. Add more reviews, then parse again.")
    return None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run_parse(job, slug: str, run_id: str, bodies: list[dict]) -> dict:
    """Tag every body, check the result, then cluster and theme it.

    Returns what the UI needs to decide where to go: `grouped` is whether there
    are results to open, and `stopped` says why not when there aren't.
    """
    plan = estimate(bodies)
    total = plan["seconds"] or 1
    split = min(0.6, max(0.1, plan["tagging_seconds"] / total))

    tag_summary = tagging.run_tagging(jobs.Phase(job, 0.0, split), slug, run_id)
    parse_id = tag_summary["parse_id"]
    out = {
        "parse_id": parse_id, "run_id": run_id, "slug": slug,
        "grouped": False, "stopped": None,
        "tagging": tag_summary, "grouping": None,
        "estimated_cost_usd": plan["cost_usd"],
        "cost_usd": tag_summary.get("cost_usd"),
    }

    reason = check_tagging(tag_summary)
    if reason:
        job.note(reason)
        out["stopped"] = reason
        return out

    try:
        group_summary = grouping.run_grouping(jobs.Phase(job, split, 1.0), slug, run_id, parse_id)
    except grouping.GroupingError as exc:
        # The tags are saved and were paid for. Say what happened and leave the
        # way open to cluster them again, instead of failing the whole job.
        reason = (f"Tagging finished, but clustering could not be completed: {exc} "
                  f"The tags are saved — press Group tags to try clustering again.")
        job.note(reason)
        out["stopped"] = reason
        return out

    theme_cost = _theme_cost(slug, run_id, parse_id)
    costs = [tag_summary.get("cost_usd"), group_summary.get("cost_usd"), theme_cost]
    out.update(
        grouped=True,
        grouping=group_summary,
        cost_usd=round(sum(c for c in costs if c), 4) if any(c is not None for c in costs) else None,
    )
    return out


def _theme_cost(slug: str, run_id: str, parse_id: str) -> float | None:
    log = storage.read_json(storage.run_dir(slug, run_id) / parse_id / "themes_log.json", {}) or {}
    return log.get("cost_usd")
