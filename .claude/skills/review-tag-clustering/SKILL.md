---
name: review-tag-clustering
description: Groups LLM-extracted tags from Amazon listings and customer reviews of home decor products (picture frames, mugs, artificial plants, vases, candles, etc.) into semantic clusters, each with a title and a one-sentence customer-facing description, and saves the result as a markdown file. Use this skill whenever the user pastes or uploads lists of product feature/benefit tags, complaint tags, or assembly/care/maintenance instruction tags and asks to group, cluster, categorize, bucket, organize, or consolidate them, even if they don't name the skill. Also trigger when the user shares a long list of short keyword-like phrases from reviews or feature bullets and wants them summarized into themes.
---

# Review Tag Clustering

Turn raw tag lists extracted from Amazon feature bullets and reviews into clean semantic groups that a customer can read. Each group gets a short title and a one-sentence description summarizing every tag in it.

## Inputs

The user may provide up to three lists. Process only the lists provided, and cluster each list separately, because a tag's meaning depends on which list it came from ("hanging hardware" is a feature in one list and a complaint in another).

1. **Product features/benefits**
2. **Complaints**
3. **Assembly, care, and maintenance instructions**

If a list isn't labeled, infer its type from its content and say which type you assumed in the chat reply. Note the product type (for example, picture frames) so descriptions can refer to it by its plain name ("frames," "mug," "plant").

## Core rules (all lists)

- **Every tag goes in exactly one group.** Before writing the file, check that the tags placed equal the tags given, with no tag missing or duplicated. Fix any mismatch before saving.
- **Copy tags verbatim.** Keep near-duplicates ("well made," "well-made"), odd phrasing, and typos exactly as given, all in the same group. The user uses the tag lists to trace back to source data.
- **Let the tags decide the number of groups.** Don't aim for a target count.
- **Split groups by distinct meaning, not by broad category.** For example, "clear, glare-free glass," "tempered protective glass," and "shatterproof acrylic" are three groups, not one "cover" group. Customers care about each difference, and a catch-all group produces a vague description.
- **Single-tag groups are fine** when nothing else shares the tag's meaning. Don't force a tag into a poor match.
- **Place ambiguous tags by their primary subject or stated benefit.** For example, "ball retaining springs prevent finger injuries" goes with safety, because the benefit it states is safety, not how the backing works. Record each such call in the Grouping Notes.

## Descriptions (all lists)

- Write exactly one sentence, in plain language a shopper would understand.
- The description must summarize all the tags in the group and claim nothing the tags don't support.
- Avoid spec numbers unless the whole group is about that spec.
- Keep titles short (2–6 words); use "&" to join two closely linked ideas.

Example (wooden serving bowls, features):
Tags: well made, durable, sturdy build, durable construction, durable for long-lasting use, very durable, well-crafted, heavy-duty bowl, thick construction, heavy build, solid-feeling weight
Title: Durability & Sturdy Build
Description: Bowl is solidly made, heavy, and built to last.

## List-specific rules

### Product features/benefits

- Group by the attribute or benefit the customer cares about: build quality, material, look/finish, style, size, safety, display options, included parts, ease of use, packaging, and so on.
- Keep related but distinct benefits apart. Examples: "genuine solid wood" (what it's made of) vs. "natural grain & finish" (how it looks); "portrait or landscape orientation" vs. "wall or tabletop display" vs. "tabletop stands"; "hardware included" vs. "easy to hang."
- Write descriptions as positive, factual statements about the product.

### Complaints

- **Group by the root underlying problem, not the specific facts or specs.** "Frame opening is not 24x36" and "mat opening is 4.5x6.5 instead of 5x7" share one root problem: the opening isn't the advertised size. They belong together even though the numbers differ.
- Ask "what went wrong for the customer?" Typical root problems include: not as advertised (wrong size or material), arrived damaged or defective, missing parts, weak or poor-quality component, hard to use or set up, poor fit, and limited options.
- Keep "the part is weak" separate from "the task is awkward" when both appear. Example: "hanging hook too small" (weak hardware) vs. "requires drilling to hang" (awkward setup).
- A tag describing a consequence of a root problem goes with that root problem. Example: "plastic cover prone to scratching" goes with "plastic instead of real glass."
- Write descriptions neutrally from the reviewers' side, starting with "Some customers..." (for example, "Some customers received frames with broken or split corners."). Don't defend the product or generalize to all buyers.

### Assembly, care, and maintenance instructions

- Group by the task the customer is doing: assembly, hanging/mounting, inserting or changing photos, cleaning, storage, handling precautions, and so on.
- Write descriptions as instructions in the imperative ("Wipe with a soft, dry cloth and avoid water or harsh cleaners."). Combine the steps and warnings from the group's tags into one sentence.
- Include only steps the tags support; don't add care advice of your own.

## Output: markdown file

Save to `/mnt/user-data/outputs/<product-type>-tag-clusters.md` (for example, `picture-frame-tag-clusters.md`), then share it with `present_files`. Within each list, **order groups from largest to smallest by tag count**. When two groups are the same size, put the more thematically related one next to its neighbors. Number groups starting from 1 in each list.

Use this template, and omit sections for lists that weren't provided:

```markdown
# Tag Clusters: [Product Type]

## Product Features & Benefits

### 1. [Group Title]
[One-sentence description.]

**Tags ([n]):** tag one, tag two, tag three

### 2. [Group Title]
...

### Grouping Notes
- [Rule or reasoning applied, phrased as a reusable guideline]
- [Judgment call: "tag" → Group X rather than Group Y, because ...]

## Complaints

(same structure, with its own Grouping Notes)

## Assembly, Care & Maintenance

(same structure, with its own Grouping Notes)
```

### Grouping Notes

Each list ends with a brief bullet list (about 3–8 bullets) of the thinking behind the grouping. The user collects these notes to improve this workflow over time, so phrase each bullet so it could become a future rule. Cover:

- The main principle used to separate groups in this list (for example, "Separated what the wood *is* from how it *looks*").
- Each borderline tag, where it went, and why.
- Groups that could reasonably be merged or split, and why you chose as you did.
- Anything unusual in the input, such as a tag that seems to belong in a different list or a tag too vague to place confidently.

## Chat reply

After presenting the file, keep the chat reply short: the number of tags and groups per list, plus any input issues the user should know about (such as an unlabeled list type you inferred). Don't repeat the groups in chat.