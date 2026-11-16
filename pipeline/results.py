"""
Step 4 — the render-ready model behind the results view.

One read of tagged.json, clusters.json and the run, joined here so the browser never
has to. Every cluster comes back with its tags already split into listing tags and
review tags, every occurrence carries the sentence it was attributed to, and every
body and product the occurrences point at is included once, keyed by id.

Nothing is written to disk. Attribution is a few milliseconds per parse, and keeping
it live means a tweak to the matcher shows up on the next reload rather than after a
re-group.
"""

from __future__ import annotations

import re
from collections import Counter

import config
from pipeline import attribution, normalize, storage, themes

PREVIEW = 3

# The four usage sub-lists, in the order they render, with their display titles.
KEYWORD_GROUPS = (("spaces", "Spaces"), ("placements", "Placements"),
                  ("occasions", "Occasions"), ("used_for", "Used for"))


def build(slug: str, run_id: str, parse_id: str) -> dict:
    directory = storage.run_dir(slug, run_id) / parse_id
    if storage.is_hidden(parse_id) or not directory.is_dir():
        raise storage.StorageError("That parse no longer exists.")

    tagged = storage.read_json(directory / "tagged.json")
    if not tagged:
        raise storage.StorageError("That parse didn't finish — nothing was written.")
    clusters = storage.read_json(directory / "clusters.json")
    if not clusters:
        raise storage.StorageError(
            "This parse hasn't been grouped yet — press Group tags on the run."
        )

    # The run supplies titles, images and review metadata. Removed ASINs come along
    # too: a tag that was extracted before the removal still needs its source.
    try:
        run = storage.load_run(slug, run_id, include_deleted=True)
    except storage.StorageError:
        run = {"products": []}
    products = {p.get("asin"): p for p in run.get("products") or []}

    bodies_ok = [b for b in tagged.get("bodies") or [] if b.get("status") == "ok"]
    attributor = attribution.Attributor(bodies_ok)
    tagged_by_id = {b["body_id"]: b for b in bodies_ok}

    occurrences: dict[str, dict] = {}
    bodies: dict[str, dict] = {}
    used_products: dict[str, dict] = {}
    sections = []

    # Themes only count if they were built from exactly these clusters. A parse
    # that was never themed, or whose theme file predates a re-grouping, simply
    # has none — the clusters render flat, as they always did.
    themes_doc = themes.read_themes(directory, clusters)
    themed = {s["key"]: s for s in (themes_doc or {}).get("sections") or []}

    for section in clusters.get("sections") or []:
        key = section.get("key")
        by_uid = {u["uid"]: u for u in section.get("unique_tags") or []}
        by_occ = {o["occ_id"]: o for o in section.get("occurrences") or []}

        built = []
        for cluster in section.get("clusters") or []:
            listing_tags, review_tags = [], []
            for uid in cluster.get("uids") or []:
                unique = by_uid.get(uid)
                if not unique:
                    continue
                split = {"listing": [], "review": []}
                for occ_id in unique["occ_ids"]:
                    occ = by_occ.get(occ_id)
                    if not occ:
                        continue
                    # Every section numbers its occurrences from o_0000, so the
                    # id is only unique once the section is part of it.
                    ref = f"{key}/{occ_id}"
                    split.setdefault(occ["body_type"], []).append(ref)
                    _register(ref, occ, occurrences, bodies, used_products,
                              attributor, tagged_by_id, products)
                for body_type, bucket in (("listing", listing_tags), ("review", review_tags)):
                    if split[body_type]:
                        bucket.append({
                            "display": unique["display"],
                            "count": len(split[body_type]),
                            "occ_ids": split[body_type],
                        })

            for bucket in (listing_tags, review_tags):
                bucket.sort(key=lambda t: (-t["count"], t["display"].lower()))

            # Preview is by total mentions across both sources, so the collapsed card
            # shows what people said most, not what listings said most.
            totals: Counter = Counter()
            first: dict[str, list] = {}
            for tag in listing_tags + review_tags:
                totals[tag["display"]] += tag["count"]
                first.setdefault(tag["display"], []).extend(tag["occ_ids"])
            preview = [
                {"display": d, "count": n, "occ_ids": first[d]}
                for d, n in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:PREVIEW]
            ]

            built.append({
                "cluster_id": cluster.get("cluster_id"),
                "number": cluster.get("number"),
                "title": cluster.get("title"),
                "description": cluster.get("description") or "",
                "total_tags": cluster.get("total_tags", 0),
                "distinct_tags": len(cluster.get("uids") or []),
                "unique_listings": cluster.get("unique_listings", 0),
                "unique_reviews": cluster.get("unique_reviews", 0),
                "unique_asins": cluster.get("unique_asins", 0),
                "listing_tags": listing_tags,
                "review_tags": review_tags,
                "preview": preview,
            })

        theme_section = themed.get(key) or {}
        sections.append({
            "key": section.get("key"),
            "title": section.get("title"),
            "status": section.get("status"),
            "error": section.get("error"),
            "mentions": len(section.get("occurrences") or []),
            "clusters": built,
            "eligible": themes.eligible(section),
            "themes": list(theme_section.get("themes") or [])
                      if theme_section.get("status") == "ok" else [],
            "theme_error": theme_section.get("error")
                           if theme_section.get("status") == "failed" else None,
        })

    # Counted before the keyword walk on purpose: these two figures mean
    # "mentions of a clustered tag", which is what the results header reports.
    attributed = sum(1 for o in occurrences.values() if o["match"]["confident"])
    mentions = len(occurrences)

    keywords = _keywords(bodies_ok, occurrences, bodies, used_products,
                         attributor, tagged_by_id, products)

    live_products = [p for p in products.values() if not p.get("deleted")]

    _attach_review_media(storage.run_dir(slug, run_id) / "raw", bodies, products)
    _photos_first(sections, occurrences, bodies)

    return {
        "slug": slug,
        "run_id": run_id,
        "parse_id": parse_id,
        "product": clusters.get("product") or slug.replace("-", " ").title(),
        "generated_at": clusters.get("generated_at"),
        "model": clusters.get("model"),
        "reasoning": clusters.get("reasoning"),
        "products_total": len(live_products) or len(products),
        "attributed": attributed,
        "mentions": mentions,
        "sections": sections,
        "keywords": keywords,
        "themes": {
            "status": "ok" if themes_doc else "missing",
            "generated_at": (themes_doc or {}).get("generated_at"),
            "model": (themes_doc or {}).get("model"),
            "reasoning": (themes_doc or {}).get("reasoning"),
            "min_clusters": (themes_doc or {}).get("min_clusters") or config.THEME_MIN_CLUSTERS,
            "eligible": [s["key"] for s in sections if s["eligible"]],
            "themed": [s["key"] for s in sections if s["themes"]],
        },
        "occurrences": occurrences,
        "bodies": bodies,
        "products": used_products,
    }


def _register(ref: str, occ: dict, occurrences: dict, bodies: dict, used_products: dict,
              attributor: attribution.Attributor, tagged_by_id: dict, products: dict) -> None:
    """Attribute one occurrence and pull in the body and product it points at."""
    body_id, asin = occ["body_id"], occ["asin"]
    occurrences[ref] = {
        "body_id": body_id,
        "asin": asin,
        "body_type": occ["body_type"],
        "text": occ["text"],
        "match": attributor.attribute(body_id, occ["text"]),
    }

    if body_id not in bodies:
        tagged = tagged_by_id.get(body_id) or {}
        tags = tagged.get("tags") or {}
        bodies[body_id] = {
            "asin": asin,
            "type": occ["body_type"],
            "text": tagged.get("text") or "",
            "review": _review_meta(products.get(asin), body_id),
            "tags": {key: list(tags.get(key) or []) for key in attribution.LISTS},
        }

    if asin not in used_products:
        p = products.get(asin) or {}
        used_products[asin] = {
            "asin": asin,
            "title": p.get("title"),
            "brand": p.get("brand"),
            "main_image": p.get("main_image"),
            "images": _gallery(p),
            "link": p.get("link") or p.get("source_url"),
            "price": (p.get("price") or {}).get("raw") if isinstance(p.get("price"), dict) else None,
            "rating": p.get("rating"),
            "ratings_total": p.get("ratings_total"),
            "deleted": bool(p.get("deleted")),
        }


def _gallery(p: dict) -> list[str]:
    """The main image, then the stored gallery, for the hover card, panel and zoom view.

    Deduped by Amazon image id (the filename up to its first "."), so the same
    picture at two size codes shows once. A manual product with no images gives [].
    """
    out, seen = [], set()
    for url in [p.get("main_image")] + list(p.get("images") or []):
        if not isinstance(url, str) or not url:
            continue
        key = url.rsplit("/", 1)[-1].split(".", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


_ARTICLE = re.compile(r"^(?:the|a|an)\s+")
_KEEP = re.compile(r"[^a-z0-9\s-]")
_EDGE_HYPHEN = re.compile(r"(?<![a-z0-9])-+|-+(?![a-z0-9])")


def _keyword_norm(text: str) -> str:
    """The key two keywords share when they are the same query.

    Lowercase, punctuation dropped except hyphens between characters, whitespace
    collapsed, a leading article removed, and a trailing plural "s" folded on
    words longer than three characters that do not end "ss"/"us"/"is".

    The plural fold is the one place this differs from `grouping.collect`, which
    deliberately keeps tags as exact strings (see grouping.py:125-127: folding
    "sturdy pot" into "sturdy pots" loses a real distinction on a product sold in
    pairs). A keyword is the opposite case — a shopper typing "picture frame" and
    one typing "picture frames" are one query, and counting them apart would
    split the ranking that makes this list worth reading.
    """
    s = _KEEP.sub(" ", (text or "").lower().strip())
    s = _EDGE_HYPHEN.sub(" ", s)
    s = " ".join(s.split())
    s = _ARTICLE.sub("", s)
    words = []
    for word in s.split():
        if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def _keywords(bodies_ok: list[dict], occurrences: dict, bodies: dict, used_products: dict,
              attributor: attribution.Attributor, tagged_by_id: dict, products: dict) -> dict:
    """Search terms and usage keywords, deduped and ranked, with provenance.

    Every mention goes through the same `_register` the cluster tags use, so a
    keyword carries the sentence it was attributed to and its body and product
    are in the response already. Refs are namespaced under `kw/` and numbered per
    list, so they can never collide with a section's own `o_0000` ids.
    """
    def collect(items: list[str], prefix: str) -> list[dict]:
        buckets: dict[str, dict] = {}
        for index, (body, text) in enumerate(items):
            ref = f"{prefix}/o_{index:04d}"
            _register(ref, {"body_id": body["body_id"], "asin": body["asin"],
                            "body_type": body["type"], "text": text},
                      occurrences, bodies, used_products, attributor, tagged_by_id, products)

            # An all-punctuation keyword would normalise to nothing; keep it
            # under its own text rather than merging every such tag into one row.
            norm = _keyword_norm(text) or text.lower()
            bucket = buckets.get(norm)
            if bucket is None:
                bucket = {"norm": norm, "occ_ids": [], "forms": Counter(), "seen": {}}
                buckets[norm] = bucket
            bucket["occ_ids"].append(ref)
            bucket["forms"][text] += 1
            bucket["seen"].setdefault(text, index)

        out = []
        for bucket in buckets.values():
            # Display is the wording most people used; ties go to the first seen.
            display = min(bucket["forms"].items(),
                          key=lambda kv: (-kv[1], bucket["seen"][kv[0]]))[0]
            out.append({"display": display, "norm": bucket["norm"],
                        "count": len(bucket["occ_ids"]), "occ_ids": bucket["occ_ids"]})
        out.sort(key=lambda i: (-i["count"], i["display"].lower()))
        return out

    def gather(key: str, sub: str | None = None) -> list:
        found = []
        for body in bodies_ok:
            tags = body.get("tags") or {}
            values = tags.get(key) or []
            if sub is not None:
                values = (values or {}).get(sub) if isinstance(values, dict) else []
            for raw in values or []:
                text = (raw or "").strip()
                if text:
                    found.append((body, text))
        return found

    return {
        "search_terms": {
            "key": "search_terms", "title": "Search Terms",
            "items": collect(gather("search_terms"), "kw/search_terms"),
        },
        "usage": {
            "key": "usage", "title": "Usage Keywords",
            "groups": [
                {"key": sub, "title": title,
                 "items": collect(gather("usage_keywords", sub), f"kw/usage/{sub}")}
                for sub, title in KEYWORD_GROUPS
            ],
        },
    }


def _review_meta(product: dict | None, body_id: str) -> dict | None:
    """Stars, date and provenance for a review body, or None if it's gone."""
    parts = body_id.split(":", 2)
    if len(parts) != 3 or parts[1] != "review" or not product:
        return None
    review_id = parts[2]
    for review in product.get("reviews") or []:
        if review.get("id") == review_id:
            return {
                "title": review.get("title"),
                "rating": review.get("rating"),
                "date": review.get("date"),
                "verified_purchase": bool(review.get("verified_purchase")),
                "source": review.get("source"),
            }
    return None


# A customer photo's address on Amazon's image host. Anything else the scraper saved
# under a review's images is not a photo: mostly the grey pixel Amazon lazy-loads
# behind, which would otherwise show up as an empty frame.
_PHOTO = re.compile(r"/images/I/([^./]+)\.[^/]*?(jpe?g|png|webp|gif)$", re.IGNORECASE)


def _photos(urls) -> list[str]:
    """A review's photos, one address each.

    The scraper keeps the same photo under more than one address (a size variant, a
    measurement prefix in the path); the id is what identifies it, so each is rewritten
    to its plain form and kept once.
    """
    photos, seen = [], set()
    for url in urls or []:
        match = _PHOTO.search(str(url).split("?")[0])
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        photos.append(f"https://m.media-amazon.com/images/I/{match.group(1)}.{match.group(2).lower()}")
    return photos


def _attach_review_media(raw_dir, bodies: dict, products: dict) -> None:
    """Give each review body its customer photos and its own page on Amazon.

    Read from the raw scrape (raw/<ASIN>.amazon_reviews.json) rather than the run, so
    a parse made before this existed gets them too. A review from Rainforest or pasted
    by hand gets them once its listing has been scraped and the scrape holds the same
    review; until then it has neither, and says so with an empty list and no link.
    """
    media: dict[str, dict] = {}
    for body_id, body in bodies.items():
        review = body.get("review")
        if not review:
            continue
        asin = body.get("asin")
        if asin not in media:
            media[asin] = media_for_reviews(
                raw_dir, asin, (products.get(asin) or {}).get("reviews") or [])
        found = media[asin].get(body_id.split(":", 2)[-1]) or {}
        review["photos"] = found.get("photos") or []
        review["author"] = found.get("author")
        review["url"] = found.get("url")


def review_media(raw_dir, asin: str) -> dict[str, dict]:
    """What the scrape holds for one listing's reviews beyond their text, by review id:
    `photos`, the reviewer's name as `author`, and the review's own page as `url`.

    Shared by the results view and the run view; {} when the listing was never scraped.
    """
    payload = storage.read_json(raw_dir / f"{asin}.amazon_reviews.json", {}) or {}
    site = payload.get("domain")
    out = {}
    for raw in payload.get("reviews") or []:
        review_id = raw.get("review_id")
        if not review_id:
            continue
        out[review_id] = {
            "body_key": normalize.body_key(normalize.clean_text(raw.get("review"))),
            "photos": _photos(raw.get("image_urls")),
            "author": raw.get("username") or None,
            # The review's permanent page, not the scraper's link, which carries the
            # listing-page context it was found in.
            "url": f"https://www.{site}/gp/customer-reviews/{review_id}" if site else None,
        }
    return out


def media_for_reviews(raw_dir, asin: str, reviews: list[dict]) -> dict[str, dict]:
    """`review_media`, keyed by the ids the run's own reviews carry.

    A review scraped from Amazon has Amazon's id and matches on it. One that came
    from Rainforest usually does too. One pasted by hand has an id of its own, so it
    is matched on its text instead — the same rule that stops a review being pasted
    twice, which also sees through a body cut off at "Read more".
    """
    scraped = review_media(raw_dir, asin)
    if not scraped:
        return {}
    by_body = {m["body_key"]: m for m in scraped.values() if m["body_key"]}
    out = {}
    for review in reviews:
        found = scraped.get(review.get("id"))
        if not found:
            found, _ = normalize.find_duplicate(normalize.body_key(review.get("body")), by_body)
        if found:
            out[review.get("id")] = {k: v for k, v in found.items() if k != "body_key"}
    return out


def _photos_first(sections: list[dict], occurrences: dict, bodies: dict) -> None:
    """Put what a customer photographed where it is seen first.

    Within a review chip, the mentions whose review has photos lead, so a click opens
    on one. Within a cluster, the chips that have any lead, then by count as before.
    `photos` on a chip is how many of its mentions carry photos.

    A collapsed card's preview stays the three things said most; among those three
    the ones with photos lead, and their mentions are ordered the same way.
    """
    def has_photos(ref: str) -> bool:
        review = (bodies.get((occurrences.get(ref) or {}).get("body_id")) or {}).get("review")
        return bool(review and review.get("photos"))

    for section in sections:
        for cluster in section.get("clusters") or []:
            for name in ("review_tags", "preview"):
                tags = cluster.get(name) or []
                for tag in tags:
                    with_photos = [ref for ref in tag["occ_ids"] if has_photos(ref)]
                    tag["photos"] = len(with_photos)
                    if with_photos:
                        lead = set(with_photos)
                        tag["occ_ids"] = with_photos + [r for r in tag["occ_ids"] if r not in lead]
                tags.sort(key=lambda t: (not t["photos"], -t["count"], t["display"].lower()))
