#!/usr/bin/env python3
"""Verify a group-of-groups file against its source clusters file.

Usage:
    python check_grouping.py <source.md> <output.md> ["Section Name" ...]

Default section: "Product Features & Benefits".
Checks, per section: every source group appears exactly once, member tag
counts match the source, and each theme's stated totals add up.
"""
import re
import sys

GROUP_RE = re.compile(r"^###\s+\d+\.\s+(.+?)\s*$")
TAGS_RE = re.compile(r"^\*\*Tags \((\d+)\):\*\*")
# "Groups" in hand-made files; "Clusters" (with an optional mentions count) in the
# themes.md the app writes. Both count members by distinct tags.
THEME_TOTAL_RE = re.compile(
    r"^\*\*(?:Groups|Clusters) \((\d+)\)\s*·\s*Tags \((\d+)\)(\s*·\s*Mentions \(\d+\))?:\*\*"
)
MEMBER_RE = re.compile(r"^-\s+(.+?)\s+\((\d+)\)\s*$")


def sections(path):
    """Return {section_name: [lines]} split on '## ' headings."""
    out, current = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f.read().replace("\r\n", "\n").split("\n"):
            if line.startswith("## "):
                current = line[3:].strip()
                out[current] = []
            elif current:
                out[current].append(line)
    return out


def parse_source(lines):
    groups, title = {}, None
    for line in lines:
        m = GROUP_RE.match(line)
        if m:
            title = m.group(1)
            continue
        m = TAGS_RE.match(line)
        if m and title:
            groups[title] = int(m.group(1))
            title = None
    return groups


def parse_output(lines):
    themes, theme = [], None
    for line in lines:
        m = GROUP_RE.match(line)
        if m:
            if m.group(1).strip() == "Grouping Notes":
                theme = None
                continue
            theme = {"title": m.group(1), "g": None, "t": None, "members": [],
                     "by_mentions": False}
            themes.append(theme)
            continue
        if theme is None:
            continue
        m = THEME_TOTAL_RE.match(line)
        if m:
            theme["g"], theme["t"] = int(m.group(1)), int(m.group(2))
            # The app orders by mentions, not distinct tags, so the tag-count
            # ordering checks below don't apply to its files.
            theme["by_mentions"] = bool(m.group(3))
            continue
        m = MEMBER_RE.match(line)
        if m:
            theme["members"].append((m.group(1), int(m.group(2))))
    return themes


def check(section, src_lines, out_lines):
    problems = []
    source = parse_source(src_lines)
    themes = parse_output(out_lines)
    seen = {}
    for th in themes:
        members = th["members"]
        if th["g"] != len(members):
            problems.append(f"Theme '{th['title']}': states {th['g']} groups, lists {len(members)}")
        total = sum(n for _, n in members)
        if th["t"] != total:
            problems.append(f"Theme '{th['title']}': states {th['t']} tags, members sum to {total}")
        counts = [n for _, n in members]
        if not th["by_mentions"] and counts != sorted(counts, reverse=True):
            problems.append(f"Theme '{th['title']}': members not ordered largest to smallest")
        for name, n in members:
            seen.setdefault(name, []).append(th["title"])
            if name not in source:
                problems.append(f"Unknown group '{name}' in theme '{th['title']}'")
            elif source[name] != n:
                problems.append(f"Group '{name}': listed {n} tags, source has {source[name]}")
    for name, where in seen.items():
        if len(where) > 1:
            problems.append(f"Group '{name}' appears in multiple themes: {where}")
    for name in source:
        if name not in seen:
            problems.append(f"Group '{name}' is missing from the output")
    totals = [th["t"] or 0 for th in themes]
    if not any(th["by_mentions"] for th in themes) and totals != sorted(totals, reverse=True):
        problems.append("Themes are not ordered largest to smallest by tag count")

    print(f"== {section} ==")
    print(f"Source: {len(source)} groups, {sum(source.values())} tags")
    print(f"Output: {len(themes)} themes, {sum(len(t['members']) for t in themes)} groups, "
          f"{sum(n for t in themes for _, n in t['members'])} tags")
    for p in problems:
        print("  PROBLEM:", p)
    if not problems:
        print("  OK")
    return not problems


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src, out = sections(sys.argv[1]), sections(sys.argv[2])
    names = sys.argv[3:] or ["Product Features & Benefits"]
    ok = True
    for name in names:
        if name not in src:
            print(f"Section '{name}' not found in source")
            ok = False
            continue
        if name not in out:
            print(f"Section '{name}' not found in output")
            ok = False
            continue
        ok = check(name, src[name], out[name]) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
