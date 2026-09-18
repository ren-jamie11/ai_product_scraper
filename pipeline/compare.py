"""
Does a different way of running the clustering produce the same clusters?

    python -m pipeline.compare <slug> <run> <parse> [--list features] [--mode partitioned]
                               [--repeat 1] [--reference tag-clustering-examples/x.md]
                               [--out <dir>]

Re-clusters one list of an existing parse — by default forcing the partitioned path
— and scores the result against the clusters already on disk (the single-call
baseline), against a second fresh run of the baseline path if `--repeat` asks for
one, and against a hand-made example file when `--reference` names one. Nothing is
written into data/: new clusterings go to `--out` (default: a folder under the
system temp dir) as JSON and as the same markdown clusters.md uses, for hand reading.

The score is pair agreement: over every pair of tags both clusterings know, the
share placed the same way (together in both, or apart in both). Two runs of the
very same path do not agree perfectly — the model is not deterministic — so the
bar for a new path is "at least as close to the baseline as the baseline is to
itself", which is what `--repeat 1` measures.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import tempfile
import time
from pathlib import Path

import config
from pipeline import grouping, storage

LIST_HEADINGS = {v: k for k, v in grouping.TITLES.items()}


# ---------------------------------------------------------------------------
# Clusterings as tag -> cluster maps
# ---------------------------------------------------------------------------

def assignment(section: dict) -> dict[str, int]:
    """display text -> cluster number, for a clusters.json section."""
    by_uid = {u["uid"]: u["display"] for u in section.get("unique_tags") or []}
    out: dict[str, int] = {}
    for n, cluster in enumerate(section.get("clusters") or []):
        for uid in cluster.get("uids") or []:
            if uid in by_uid:
                out[by_uid[uid]] = n
    return out


def assignment_from_markdown(path: Path, key: str) -> dict[str, int]:
    """display text -> cluster number, from a hand-made tag-clusters markdown file."""
    text = Path(path).read_text(encoding="utf-8")
    wanted = grouping.TITLES[key]
    out: dict[str, int] = {}
    section, number = None, -1
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section != wanted:
            continue
        if line.startswith("### "):
            number += 1
            continue
        m = re.match(r"\*\*Tags \(\d+\):\*\*\s*(.*)", line.strip())
        if m and number >= 0:
            for tag in m.group(1).split(","):
                tag = tag.strip()
                if tag:
                    out[tag] = number
    return out


def pair_agreement(a: dict[str, int], b: dict[str, int]) -> dict:
    shared = sorted(set(a) & set(b))
    together_both = apart_both = a_only = b_only = 0
    for x, y in itertools.combinations(shared, 2):
        same_a = a[x] == a[y]
        same_b = b[x] == b[y]
        if same_a and same_b:
            together_both += 1
        elif not same_a and not same_b:
            apart_both += 1
        elif same_a:
            a_only += 1
        else:
            b_only += 1
    pairs = together_both + apart_both + a_only + b_only
    return {
        "shared_tags": len(shared),
        "pairs": pairs,
        "agreement": round((together_both + apart_both) / pairs, 4) if pairs else None,
        "together_in_a_only": a_only,
        "together_in_b_only": b_only,
        "clusters_a": len(set(a[t] for t in shared)) if shared else 0,
        "clusters_b": len(set(b[t] for t in shared)) if shared else 0,
    }


# ---------------------------------------------------------------------------
# Running a clustering without touching data/
# ---------------------------------------------------------------------------

def regroup(key: str, uniques: list[dict], occurrences: list[dict], product: str,
            mode: str) -> tuple[dict, float]:
    """One fresh clustering of the list on the requested path. Priors are empty so
    both paths start from nothing and neither is anchored to the titles on disk."""
    prompt, _ = grouping.load_prompt()
    choice = grouping.group_choice()
    previous = config.GROUP_SINGLE_CALL_MAX
    config.GROUP_SINGLE_CALL_MAX = 0 if mode == "partitioned" else 10 ** 9
    started = time.perf_counter()
    try:
        section = grouping.group_list(key, uniques, occurrences, product, prompt, choice, [])
    finally:
        config.GROUP_SINGLE_CALL_MAX = previous
    section["occurrences"] = occurrences
    section["unique_tags"] = uniques
    return section, round(time.perf_counter() - started, 1)


def write_section(out_dir: Path, name: str, product: str, section: dict) -> None:
    doc = {"product": product, "sections": [section]}
    storage.write_json(out_dir / f"{name}.json", doc)
    (out_dir / f"{name}.md").write_text(grouping.render_markdown(doc), encoding="utf-8")


def describe(section: dict) -> str:
    if section.get("status") != "ok":
        return f"FAILED: {section.get('error')}"
    clusters = section.get("clusters") or []
    sizes = sorted((len(c.get("uids") or []) for c in clusters), reverse=True)
    parts = section.get("partitions") or {}
    bits = [f"{len(clusters)} clusters", f"largest {sizes[0] if sizes else 0}",
            f"singletons {sum(1 for s in sizes if s == 1)}",
            f"repairs {len(section.get('repairs') or [])}",
            f"tokens out {section.get('usage', {}).get('output_tokens')}"]
    if parts:
        bits.append(f"partitions {parts.get('count')} sizes {parts.get('sizes')} "
                    f"merge {parts.get('clusters_before_merge')}→{parts.get('clusters_after_merge')}")
    return " · ".join(bits)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("run_id")
    ap.add_argument("parse_id")
    ap.add_argument("--list", default="features", choices=list(grouping.LISTS))
    ap.add_argument("--mode", default="partitioned", choices=["partitioned", "single"],
                    help="which path to run fresh (default: partitioned)")
    ap.add_argument("--repeat", type=int, default=0,
                    help="also run the single-call path fresh this many times, to measure "
                         "the baseline's own run-to-run agreement")
    ap.add_argument("--reference", help="a hand-made tag-clusters .md file to score against")
    ap.add_argument("--out", help="folder for the new clusterings (default: temp dir)")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="compare-"))
    out_dir.mkdir(parents=True, exist_ok=True)

    tagged = grouping.load_tagged(args.slug, args.run_id, args.parse_id)
    product = (storage.get_group(args.slug).get("name")
               or args.slug.replace("-", " ").title())
    occurrences, uniques = grouping.collect(tagged, args.list)
    if not uniques:
        raise SystemExit(f"{args.slug} has no {args.list} tags in that parse.")

    on_disk = storage.read_json(storage.run_dir(args.slug, args.run_id) / args.parse_id / "clusters.json")
    baseline = next((s for s in (on_disk or {}).get("sections") or [] if s.get("key") == args.list), None)
    if baseline and baseline.get("status") == "ok":
        print(f"baseline (on disk, {baseline.get('mode', 'single')}): {describe(baseline)}")
    else:
        print("baseline (on disk): none — this list was never grouped successfully")
        baseline = None

    print(f"\nrunning {args.mode} on {len(uniques)} unique {args.list} tags…")
    fresh, seconds = regroup(args.list, uniques, occurrences, product, args.mode)
    print(f"{args.mode} ({seconds}s): {describe(fresh)}")
    write_section(out_dir, args.mode, product, fresh)
    for note in fresh.get("notes") or []:
        print("  note:", note)

    repeats = []
    for i in range(args.repeat):
        print(f"\nrunning single-call repeat {i + 1}…")
        again, seconds = regroup(args.list, uniques, occurrences, product, "single")
        print(f"single repeat {i + 1} ({seconds}s): {describe(again)}")
        write_section(out_dir, f"single-repeat-{i + 1}", product, again)
        repeats.append(again)

    print("\n--- pair agreement ---")
    if fresh.get("status") == "ok":
        a = assignment(fresh)
        if baseline:
            print(f"{args.mode} vs baseline:      {pair_agreement(a, assignment(baseline))}")
        for i, again in enumerate(repeats, 1):
            if again.get("status") == "ok":
                print(f"{args.mode} vs single repeat {i}: {pair_agreement(a, assignment(again))}")
        if args.reference:
            ref = assignment_from_markdown(Path(args.reference), args.list)
            print(f"{args.mode} vs reference:     {pair_agreement(a, ref)}")
    if baseline:
        b = assignment(baseline)
        for i, again in enumerate(repeats, 1):
            if again.get("status") == "ok":
                print(f"baseline vs single repeat {i}: {pair_agreement(b, assignment(again))}")
        if args.reference:
            ref = assignment_from_markdown(Path(args.reference), args.list)
            print(f"baseline vs reference:      {pair_agreement(b, ref)}")

    print(f"\nnew clusterings written to {out_dir}")


if __name__ == "__main__":
    main()
