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

from collections import Counter

from pipeline import attribution, storage

PREVIEW = 3


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

        sections.append({
            "key": section.get("key"),
            "title": section.get("title"),
            "status": section.get("status"),
            "error": section.get("error"),
            "mentions": len(section.get("occurrences") or []),
            "clusters": built,
        })

    attributed = sum(1 for o in occurrences.values() if o["match"]["confident"])
    live_products = [p for p in products.values() if not p.get("deleted")]

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
        "mentions": len(occurrences),
        "sections": sections,
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
            "link": p.get("link") or p.get("source_url"),
            "price": (p.get("price") or {}).get("raw") if isinstance(p.get("price"), dict) else None,
            "rating": p.get("rating"),
            "ratings_total": p.get("ratings_total"),
            "deleted": bool(p.get("deleted")),
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
