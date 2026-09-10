---
name: product-research-extractor
description: Extracts search terms, concrete feature benefits, and usage keywords from Amazon listings and customer reviews for home decor products (picture frames, artificial trees and flowers, vases, mugs, candles, lamps, wall art, etc.). Use this skill whenever the user pastes or uploads an Amazon listing, title, bullet points, A+ copy, or a block of customer reviews and asks to extract, parse, pull, mine, or analyze search terms, keywords, features, benefits, or selling points — even if they don't name the skill. Also trigger for phrasing like "what are the key features here", "pull the keywords from this listing", "run product research on this", or when the user is doing competitor/listing research and shares source text. Use it even for a single listing or a single review.
---
 
# Amazon Product Research Extractor
 
## Task
From an Amazon listing or a block of customer reviews for a home decor product, extract search terms, concrete feature benefits, and usage keywords. Report only what the source states.
 
## Output format
```
Search terms
<comma-separated>
 
Feature benefits
<comma-separated>
 
Usage keywords
Spaces:
Placements:
Occasions:
Used for:
 
Avoided
"<phrase>" — <one-line reason>
```
Omit any section or category the source yields nothing for.
 
## Search terms
- Noun phrases a shopper would type. 2–5 words.
- Must contain the product noun (mug, picture frame, olive tree) or its category (artificial flower, indoor home decor).
- Pull from titles, bullet headers, and review wording alike — reviews can supply search terms too.
- SKU specifics are allowed here: "5x7 picture frames", "picture frames set of 4".
## Feature benefits
Every label must be clear, direct, concise, and complete on its own — a reader seeing it cold should need no further context — and cover one product component or one theme.
 
### What qualifies
- Concrete, factual, distinct: textured green leaves, no sunlight, water, or pruning, microwave and dishwasher safe.
- Exclude vague praise: looks beautiful, great quality, well designed, looks fantastic.
- Exclude price and value claims: great price, affordable, perfect for the money.
- Exclude listing-fidelity claims: exactly as described, as pictured.
- Exclude SKU specs that don't generalize: color, shape, size, pack count.
- Exclude reviewer framing that yields no product attribute — "I like candid shots in smaller frames" describes the shopper, not the frame. When the shopper's situation does reveal a capability, keep the capability (see below).
### How to word the label
 
**Paraphrase verbose or indirect wording.** When the source is wordy, roundabout, or grammatically weak, state the underlying benefit plainly. When it's already clean and direct, keep it as-is.
- "I don't have the largest space to do my Pilates but this mat works just fine in that space" → suitable for small spaces
- "it does look good enough because I don't have the time to keep a real tree alive" → no maintenance required
- "no worrying about injury for broken glass" → shatter-resistant
**Name the component when the attribute needs it.** A bare attribute is incomplete if the reader can't tell what it describes. Attach the component. If the attribute applies to the whole product, or the source never says what it modifies, leave it bare rather than guessing.
- "The tempered glass is clear and transparent, scratch-resistant" → tempered glass, clear transparent glass, scratch-resistant glass. Not: clear and transparent, scratch-resistant.
- "Well made, finish was pretty, easy to use, and scratch-resistant" → well made, pretty finish, easy to use, scratch-resistant. The first three describe the whole product; the source never says what is scratch-resistant, so it stays bare.
**Group or split by theme.** Keep causally linked or same-theme attributes in one label; split independent benefits apart.
- "the mat is thick and cushiony" → thick and cushiony (thick is why it's cushiony)
- "I love this mat. Strong grip and easy to use" → strong grip, easy to use (independent)
- "No watering, pruning, or sunlight required" → no watering, pruning, or sunlight (one maintenance theme)
- "Love the leaves and the olives and that the trunk has a wood-like appearance" → wood-like trunk. The leaves and olives are a different component, and the source never says what's good about them.
## Usage keywords
1–2 words each.
- **Spaces** — rooms or environments: bedroom, living room, office, patio.
- **Placements** — surfaces or locations within a space: shelf, desk, mantel, nightstand, dresser, end table.
- **Occasions** — Christmas, wedding, birthday, Mother's Day. Capture every occasion named, including long gift-holiday lists.
- **Used for** — what the product holds, displays, or works with: picture frame → wedding photos, pet pictures, art prints; mug → coffee, tea, latte; vase → fresh flowers, pampas grass, dried plants.
Rules for all four:
- Skip generic filler: home, space, room, area, decor.
- Modifiers must be factually concrete: "vintage photo" and "family photo" qualify; "cherished photo" and "decorative photo" do not.
## Faithfulness
- Paraphrase to clarify, never to add. Every label must trace to a specific line of the source; if you can't point to the line it came from, drop it.
- Rewriting for clarity is expected (see the wording rules above). Inventing benefits, upgrading a modest claim into a stronger one, or filling in a component the source left unstated is not.
- Also fine: singular/plural, dropping filler ("Our", "We provide"), collapsing repetition — "hanging them was easy" → "easy to hang".
- Read every line and capture everything that qualifies.
- Deduplicate: one entry per distinct benefit, even when reviewers phrase it differently.
- If the source contradicts itself on a factual detail (e.g. title says PS, bullet says PVC), carry both and flag the contradiction rather than picking one.
## Avoided
List each phrase you rejected with a short reason, e.g.:
- "adds sophistication to the décor" — vague
- "price is fantastic" — price claim
- "exactly as description" — listing fidelity
## Example
 
**Input (listing):**
```
5x7 Picture Frames Set of 4, Rustic Retro Photo Frame (Mix Color)
HD Plastic Cover, Wall Mount and Tabletop Display, Family Friends Wedding Gift
 
- Vintage PS picture frame - The PS environmental protection photo frame with
  wood grain design is adopted, which is more beautiful, abrasion resistant and
  moisture-proof.
- HD plastic cover: The picture frames are safe and durable. The HD plastic cover
  can clearly display your cherished photos (No worrying about injury for broken
  glass).
- Wall Mounting & Tabletop Display - Decorative photo frames have an easel
  kickstand and hanging hooks on the back, which adds sophistication to the décor
  of your room. Your home or office will sparkle with your artful photographs.
- Nice Present Idea - Our vintage frame is a nice present for Valentines Day,
  Christmas, Mother's Day, Anniversary, Birthday etc.
```
 
**Output:**
```
Search terms
5x7 picture frames, picture frames set of 4, retro photo frame, vintage PS
picture frame, environmental protection photo frame, photo frame with wood grain,
decorative photo frame
 
Feature benefits
environmental protection PS material, wood grain design, abrasion-resistant and
moisture-proof frame, safe and durable, HD plastic cover displays photos clearly,
shatter-resistant plastic cover, easel kickstand and hanging hooks on back, wall
mounting and tabletop display
 
Usage keywords
Spaces: office
Occasions: wedding, Valentine's Day, Christmas, Mother's Day, anniversary, birthday
Used for: photos, photographs
 
Avoided
"more beautiful" — vague
"adds sophistication to the décor" — vague
"home or office will sparkle" — vague
"nice present" — gift puffery, no concrete benefit
"cherished photos" — subjective modifier; kept as "photos"
"no worrying about injury for broken glass" — indirect; paraphrased to
  "shatter-resistant plastic cover"
"mix color" — SKU-specific color
"your room", "home" — generic space
```