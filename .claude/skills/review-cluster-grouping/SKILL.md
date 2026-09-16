---
name: review-cluster-grouping
description: Groups existing tag clusters (the output of review-tag-clustering, usually a clusters.md file) into a smaller set of higher-level themes, a "group of groups," for home decor products such as picture frames, artificial trees and plants, mugs, vases, and candles. Each theme gets a concise title and a one-sentence summary, and the result is saved as a markdown file. Use this skill whenever the user uploads or pastes a clusters file and asks to group the groups, consolidate, merge, roll up, or organize similar clusters into themes or meta-groups, or says the clusters are too many, too similar, or overlapping, even if they don't name the skill.
---

# Review Cluster Grouping

Take an existing clusters file, where tags from Amazon listings and reviews have already been grouped, and combine related groups into higher-level themes. The logic mirrors tag clustering, one level up: each group is the unit, and each theme gets a short title and a one-sentence summary.

## Inputs

The input is a markdown file produced by review-tag-clustering. It may contain up to three lists, each with numbered groups (`### N. Title`), a one-sentence description, and a tag list (`**Tags (n):** ...`):

1. **Product Features & Benefits**
2. **Complaints**
3. **Assembly, Care & Maintenance**

**Read the file from disk** (usually `/mnt/user-data/uploads/clusters.md`). When the user uploads a new file with the same name, the content in context may be stale or missing, so always read the current file. Files may use Windows line endings.

**Scope:** Process only the lists the user asks for. If the user doesn't say, process **Product Features & Benefits** only, and mention in the chat reply that the other lists can be grouped on request. Group each list separately; never mix groups from different lists.

Note the product type from the file's title (for example, "Tag Clusters: Olive Trees") for the output heading and file name.

## Core rules

- **Use all of a group's information.** Judge each group by its title, description, *and* tags together. Titles can mislead; the tags show what customers actually meant (for example, "Durable, Sturdy Build" whose tags keep mentioning wood belongs with wood construction).
- **Every group goes in exactly one theme.** No group is dropped or listed twice. Before saving, confirm the group count and total tag count match the source (run `scripts/check_grouping.py`, below).
- **Copy group titles verbatim** with their original tag counts, so the user can trace each theme back to the clusters file. Don't rename or re-list tags.
- **Group by the customer need the groups serve**, such as where it displays, what it's made of, how solidly it's built, how it looks, what size it is or what fits in it, how easy it is to set up or use, what upkeep it needs, and what comes in the box.
- **Let the groups decide the number of themes.** Don't aim for a target count. In practice, 25–35 groups usually become 7–10 themes.
- **Fold small and single-tag groups into the closest theme.** Leave one standalone only if it truly fits nowhere.
- **Merge groups when one depends on the other.** Example: "Full, Balanced Foliage" joins "Bendable, Shapeable Branches" because its tags say fullness comes from fluffing and shaping.
- **Merge bridging groups with what they bridge.** Example: "Magnetic or Tabletop Display" joins the magnetic and tabletop groups in one display theme.
- **Keep distinct needs apart, even when related.** Examples: what the material *is* and how solidly it's built vs. how it *looks*; which photo sizes fit vs. the included mats that adjust the opening.
- **Split an oversized theme when it has a natural seam.** Example: realism split into the overall impression ("looks real from a distance") vs. specific component details (bark, leaves, fruit). Name the seam in the Grouping Notes so the user can merge instead.
- **Place ambiguous groups by their stated benefit**, not the component they mention. Examples: "Secure, Shed-Free Foliage" goes with long-lasting greenery (no shedding over time), not build; "Secure, Seamless Branch Connections" goes with assembly (how sections join during setup).
- **Don't let a member contradict its theme.** Example: "Lightweight Design" (easier handling and placement) goes under placement, not a heavy, stable-base theme.
- **Out-of-box extras belong together.** Packaging, included containers, and container care (for example, an easy-to-clean basket) form one ready-to-display theme.

## Titles and summaries

- **Title:** concise, 2–6 words; use "&" or a comma to join two closely linked ideas (for example, "Secure, Easy Photo Loading," "Hassle-Free, Long-Lasting Greenery").
- **Summary:** exactly one plain-language sentence that covers every member group and claims nothing the groups don't support. Refer to the product by its plain name ("frames," "trees").
- **Features:** positive, factual statements about the product.
- **Complaints** (only if requested): group by root problem, and start the summary with "Some customers..." without defending the product.
- **Assembly, care & maintenance** (only if requested): group by the task, and write the summary as an imperative instruction built only from the member groups.

## Ordering

- Order themes from largest to smallest by **total tag count**, numbered from 1 within each list.
- Within a theme, list member groups from largest to smallest by tag count.
- For ties, put the more thematically related item next to its neighbors.

## Verification

After drafting the output file, run:

```bash
python /mnt/skills/user/review-cluster-grouping/scripts/check_grouping.py <source.md> <output.md> "Product Features & Benefits"
```

(Use the skill's actual install path if different, and pass each processed list's section name.) The script reports missing, duplicated, or unknown group titles, tag-count mismatches, and theme totals that don't add up. Fix every issue before presenting the file.

## Output: markdown file

Save to `/mnt/user-data/outputs/<product-type>-group-clusters.md` (for example, `olive-tree-group-clusters.md`), then share it with `present_files`. Use this template, and omit sections for lists that weren't processed:

```markdown
# Group-of-Groups: [Product Type]

## Product Features & Benefits

### 1. [Theme Title]
[One-sentence summary.]

**Groups ([g]) · Tags ([n]):**
- [Member Group Title] ([n])
- [Member Group Title] ([n])

### 2. [Theme Title]
...

### Grouping Notes
- [Principle or judgment call, phrased as a reusable guideline]
- All [G] groups ([T] tags) are assigned to exactly one theme.

## Complaints

(same structure, with its own Grouping Notes)
```

### Grouping Notes

End each list with about 5–8 bullets that the user can turn into future rules. Cover:

- The main principle used to separate themes.
- Each borderline group, where it went, and why.
- Themes that could reasonably be merged or split, with the resulting tag count (for example, "the two realism themes merge cleanly into one 68-tag theme").
- A final line confirming the group and tag totals.

## Chat reply

After presenting the file, keep the reply short, in prose: the number of groups and tags in, the number of themes out, confirmation that every group is used exactly once, and the single biggest judgment call with its merge or split alternative. Point to the Grouping Notes for the rest. Don't repeat the themes in chat.
