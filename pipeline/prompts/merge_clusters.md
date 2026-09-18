# Amazon Product Research — merging clusters made in separate batches

A long list of tags, extracted from Amazon listings and customer reviews for a single home
decor product category, was too long to cluster in one pass. It was split into batches of
related tags and each batch was clustered on its own, by the same rules. Because the
batches never saw each other, **the same customer concern may now appear as two or more
clusters**. Your job is to find those and join them — and to leave everything else alone.

You receive a numbered list of clusters, each with its title, its one-sentence description
and every tag in it. Return JSON matching the supplied schema: an array of clusters, each
with a `title`, a `description`, and the `indices` of the input clusters it is made of.

---

## The two rules that override everything

**1. Every index appears exactly once.** Across all the clusters you return, the set of
indices must equal the set of indices you were given — nothing missing, nothing twice.
Count before you answer.

**2. Never move a tag.** You only ever emit cluster index numbers. You cannot take a tag
out of one cluster and put it in another, cannot split a cluster, and cannot drop one. A
cluster you leave alone is returned as a one-index cluster with its title and description
copied exactly.

---

## When two clusters are one

The test is the same one that built the clusters:

> **Are these the same thing a customer cares about?**

Not "are these about the same part", and not "do these use similar words". Read the tags,
not just the titles: two clusters titled differently whose tags are all about the bowl
being solidly made are one cluster. Two clusters titled alike whose tags are about
different worries are not.

| join | because |
|---|---|
| **Durability & Sturdy Build** (`well made`, `sturdy bowl`, `thick build`) + **Heavy, Solid Feel** (`heavy bowl`, `solid-feeling weight`) | one question — *is this solidly made?* |
| **No Watering Needed** + **Zero Maintenance Flowers** | one question — *what upkeep does it need?* |
| **Dishwasher Safe** + **Microwave & Oven Safe** | one cluster of what the mug can go in |

| keep apart | because |
|---|---|
| **Sturdy, Stable Base** (won't tip) and **Sturdy Branches** (hold their shape) | same adjective, two different worries |
| **Real Glass Front** and **Clear, Glare-Free Viewing** | what it is made of vs. how it looks through |
| **Stainless Steel Construction** (`metal legs`, `304 stainless steel guardrails`) and **Durability & Sturdy Build** (`well made`, `resists deformation`) | what it is made of vs. how solidly it is made — even when the material tags say "solid steel" |
| **Attractive Color Choices** (`nice color`, `multiple color options`) and **Modern Shape & Finish** (`sleek modern design`, `nice coating`) | the color vs. the style and surface — two different questions about how it looks |
| **Expandable Drying Area** (`extends across sink`) and **Adjustable Feet for Sinks** (`adjustable legs fit different sink edges`) | both adjust, but one answers "how much can I dry?" and the other "will it sit on my sink?" |
| **Hanging Hardware Included** and **Easy to Hang** | the part you get vs. the task you do |
| **Plastic Instead of Real Glass** (complaint) and **Arrived Scratched** (complaint) | two root problems, even if both mention the cover |

**When in doubt, keep them apart.** Two clusters that say nearly the same thing cost the
reader a moment; one cluster that says two things costs them the meaning. Never join
clusters to tidy the list, to reach a count, or because they are both small. There is no
catch-all: no Miscellaneous, Other, or General.

**Joining three or more clusters is rare.** Each extra member has to answer the very same
question as the first two; a cluster that only *relates* to the theme (the material the
sturdy thing is made of, the color of the modern-looking thing, the feet of the thing that
expands) stays on its own.

**Join a consequence to its root** (complaints): a cluster about the cover scratching
belongs with the cluster about the cover being plastic, because the scratching is why the
plastic matters.

---

## Titles and descriptions of a joined cluster

- Keep the title of the **largest** member unless another member's title is plainly the
  better name for the whole. Never invent a new attribute: the title must be built from
  the language of the tags.
- Write **one sentence** that covers every tag in every member and claims nothing they
  don't support, in the voice of the list: positive fact for `features`; "Some
  customers…" for `complaints`; an imperative instruction for `assembly_maintenance`.
- A cluster you did not join keeps its title and description **exactly as given**.

---

## Prior titles

You may be given the cluster titles from the last time this category was grouped. When a
joined cluster means the same thing as one of them, **reuse that title exactly** so results
stay comparable between runs. Never join clusters to fit a prior title, and never emit a
prior title with no clusters.

---

## Worked example

Input (`features`, wooden serving bowls):

```
0. Durability & Sturdy Build — Bowl is solidly made and built to last.
   Tags (3): well made, sturdy bowl, thick build
1. Natural Grain & Warm Color — Wood shows its natural grain in a warm honey tone.
   Tags (2): natural wood grain, warm honey color
2. Heavy, Solid Feel — Bowl feels substantial in the hand.
   Tags (2): heavy bowl, solid-feeling weight
3. Handmade Craftsmanship — Each bowl is made by hand, so no two are quite alike.
   Tags (2): handmade by artisans, each bowl is unique
4. Odor-Free Wood — The wood has no smell.
   Tags (1): odorless wood
```

Output:

```json
{"clusters": [
  {"title": "Durability & Sturdy Build",
   "description": "Bowl is solidly made, heavy, and built to last.",
   "indices": [0, 2]},
  {"title": "Natural Grain & Warm Color",
   "description": "Wood shows its natural grain in a warm honey tone.",
   "indices": [1]},
  {"title": "Handmade Craftsmanship",
   "description": "Each bowl is made by hand, so no two are quite alike.",
   "indices": [3]},
  {"title": "Odor-Free Wood",
   "description": "The wood has no smell.",
   "indices": [4]}
]}
```

Three things to notice. Clusters 0 and 2 were joined because *heavy* and *solid-feeling*
answer the same "is it solidly made?" question as *sturdy* and *thick*, and the joined
description now covers the weight. Cluster 4 stayed a singleton: nothing else is about
smell, and a singleton is the right home for it. And every index appears exactly once.
