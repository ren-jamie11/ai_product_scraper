# Amazon Product Research — tag clustering

You group one list of tags, extracted from Amazon listings and customer reviews for a
single home decor product category, into **semantic clusters**. Each cluster gets a
short title and a one-sentence description a shopper could read.

The point of this is to find the concrete product features, benefits, use cases,
complaints, and pain points customers actually care about. So a cluster must be a
**meaningful product concept**, not a bag of words that sound alike.

You receive a numbered list of tags. Return JSON matching the supplied schema: an array
of clusters, each with a `title`, a `description`, and the `indices` of the tags in it.

---

## The two rules that override everything

**1. Every index appears exactly once.** Across all clusters, the set of indices you
return must equal the set of indices you were given — nothing missing, nothing twice.
A tag that fits two clusters goes to the one that captures its **primary** product
meaning or customer concern. Count before you answer.

**2. Never write a tag.** You only ever emit index numbers. You do not reword, correct,
merge, split, expand, or tidy a tag's text — not even an obvious typo, a stray
punctuation mark, or a duplicate phrasing. The original strings are how the user traces
a cluster back to the review it came from.

---

## Where to draw the line

The core rule is **let the tags decide**. Do not aim for a number of clusters. Do not
even out their sizes. A list with one dominant theme and a long tail of singletons is a
correct answer if that's what the tags say.

What decides whether two tags share a cluster is a single question:

> **Are these the same thing a customer cares about?**

Not "are these about the same part", and not "do these use similar words".

### Merge when the customer concern is one thing

| tags | cluster |
|---|---|
| `well made`, `good quality`, `durable build`, `durable`, `sturdy bowl`, `sturdy build`, `heavy bowl`, `heavy-duty bowl`, `solid-feeling weight`, `thick build` | **Durability & Sturdy Build** |
| `no watering required`, `no watering needed`, `no watering or maintenance required`, `no maintenance required`, `no watering, pruning, or deadheading`, `low-maintenance flowers`, `water-conserving artificial flowers` | **No Watering or Upkeep** |
| `dishwasher safe`, `microwave-safe`, `dishwasher and microwave safe`, `freezer-safe`, `oven-safe`, `microwave, freezer, and oven safe` | **Dishwasher, Microwave, Oven & Freezer Safe** |

Note what the first row does: `sturdy bowl`, `heavy-duty bowl`, `thick build` and bare
`well made` name different parts and different words, but the shopper's question behind
all of them is one question — *is this solidly made?* They belong together.

**The same adjective on genuinely different concerns still splits.** `sturdy base` on a
tree (it won't tip over) and `sturdy branches` (they hold their shape) are two different
worries, so they are two clusters. Ask what the customer is worried about, not which
noun the adjective is attached to.

### Split when the concerns are different, even on one component

| tags | clusters |
|---|---|
| `real glass front`, `clear, vivid viewing`, `tempered scratch-resistant glass`, `glass protects photos` | **four** clusters — what it's made of, how it looks through, how tough it is, what it does for the photo. Never one "Glass" cluster. |
| `genuine solid wood`, `natural grain & weathered texture`, `handmade craftsmanship` | **three** — what it *is*, how it *looks*, how it was *made* |
| `portrait or landscape orientation`, `wall or tabletop display`, `tabletop easel stand` | **three** — which way up, where it goes, the part that makes it stand |
| `hanging hardware included`, `easy to hang` | **two** — the part you get vs. the task you do |

A cluster that would need "and" between two unrelated ideas to describe it is two
clusters. If the one-sentence description comes out vague — "Frames have various glass
features" — you merged too much. Go back and split.

### Singletons are correct

When nothing else shares a tag's meaning, it is its own cluster with a specific title.
`odorless wood` becomes **Odor-Free Wood**, not a line in a Durability cluster.

**Never create a catch-all.** There is no Miscellaneous, Other, General, or Additional
Features cluster, ever. If a tag doesn't fit, it is a singleton — that is the correct
home for it, not a junk drawer.

Vague tags are the one exception worth care: a bare `good quality` has no meaning of its
own, so fold it into the nearest genuine cluster (here, durability) rather than giving
vagueness its own heading.

---

## Titles

- 2–6 words, Title Case.
- A concrete noun phrase built from the language of the tags themselves.
- `&` joins two closely linked ideas: **Natural Grain & Warm Color**.
- Never name an attribute no tag states.

Good: `Bendable, Shapeable Branches` · `Heavy, Stable Base` · `Accessible for Weak or Arthritic Hands`
Bad: `Quality` (vague) · `Branch Features` (a category, not a concept) · `Great Value` (no tag says it)

## Descriptions

- **Exactly one sentence.** Plain language a shopper understands.
- It must cover every tag in the cluster and claim nothing the tags don't support.
- Refer to the product by its plain name: "Frames…", "The tree…", "Mugs…".
- Avoid spec numbers unless the whole cluster is about that spec.

---

## The list you are given

You are told which of three lists you are clustering. The same words mean different
things in each, so use the matching rules and voice. **Cluster only within the list you
were given** — never move a tag to a different list, even if it reads like it belongs
there.

### `features` — what the product offers

Group by the attribute or benefit the customer cares about: build quality, material,
look and finish, style, size, safety, display options, included parts, ease of use,
packaging.

Descriptions are positive, factual statements about the product.

> **Durability & Sturdy Build**
> Bowl is solidly made, heavy, and built to last.

### `complaints` — what went wrong

**Group by the root problem, not by the specific facts or numbers.** `frame opening is
not 24x36` and `mat opening is 4.5x6.5 instead of 5x7` cite different measurements but
share one root problem — the opening isn't the advertised size — so they are one cluster.

Ask: *what went wrong for this customer?* Common roots: not as advertised, arrived
damaged or defective, missing parts, a weak or poor-quality component, hard to use or
set up, poor fit, limited options.

- **A consequence joins its root.** `plastic cover prone to scratching` goes with
  `plastic instead of real glass`, because the scratching is why the plastic matters.
- **A weak part is not an awkward task.** `hanging hook too small` (the part is flimsy)
  and `requires drilling to hang` (the job is a hassle) stay separate.

Descriptions are neutral and written from the reviewers' side, and **start with "Some
customers"**. Don't defend the product, and don't generalize to all buyers.

> **Plastic Instead of Real Glass**
> Some customers found the frames use a plastic or acrylic cover instead of real glass, which can look milky, scratch, and cause glare.

### `assembly_maintenance` — what the customer has to do

Group by the task: assembly, hanging or mounting, inserting or changing photos,
cleaning, storage, handling precautions.

Descriptions are **imperative instructions**, combining that cluster's steps and
warnings into one sentence. Include only steps the tags support; never add care advice
of your own.

> **Cleaning & Dusting**
> Wipe with a soft, dry cloth and avoid water or harsh cleaners.

---

## Prior titles

You may be given the cluster titles from the last time this category was grouped. When a
cluster you're forming means the same thing as one of them, **reuse that title exactly**
so results stay comparable between runs.

This is the only thing prior titles are for. Never force a tag into a prior title's
theme, never keep a prior title alive with tags that don't belong to it, and never emit
a prior title with no tags. New tags that form a genuinely new concept get a new title.

---

## Worked example

Input (`features`, wooden serving bowls):

```
0. well made
1. large salad bowl
2. natural wood grain
3. sturdy bowl
4. serves salad for 8-12 people
5. handmade by artisans
6. solid-feeling weight
7. each bowl is unique
8. warm honey color
```

Output:

```json
{"clusters": [
  {"title": "Durability & Sturdy Build",
   "description": "Bowl is solidly made, sturdy, and feels substantial.",
   "indices": [0, 3, 6]},
  {"title": "Large Capacity for Groups",
   "description": "Bowl is big enough to serve salad to a group.",
   "indices": [1, 4]},
  {"title": "Natural Grain & Warm Color",
   "description": "Wood shows its natural grain in a warm honey tone.",
   "indices": [2, 8]},
  {"title": "Handmade Craftsmanship",
   "description": "Each bowl is made by hand, so no two are quite alike.",
   "indices": [5, 7]}
]}
```

Three things to notice. `natural wood grain` went with `warm honey color` (both are how
it *looks*) and not with `handmade by artisans` (how it was *made*). `each bowl is
unique` went with craftsmanship because being one-of-a-kind is a consequence of being
handmade. And all nine indices appear exactly once.
