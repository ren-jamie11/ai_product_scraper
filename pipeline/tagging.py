"""
Step 2 — turning each text body into structured tags.

One OpenAI call per body, which is the whole design. A listing and each of its
reviews are tagged independently so that every tag carries an unambiguous
`body_id` back to the exact sentence that produced it. Phase 4 groups tags and
Phase 5 renders them, but neither can recover provenance this step throws away,
so `tagged.json` keeps the tag strings exactly as the model emitted them.

The rules themselves live in prompts/tag_body.md, not here. This module is only
the machinery: fan out, retry, record, and never let one bad body take down a
run. Partial success beats total failure, and every failure is reported in plain
language rather than as a stack trace.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from pipeline import normalize, settings, storage

PROMPT_PATH = Path(__file__).parent / "prompts" / "tag_body.md"

# Reasons the `avoided` field may cite. Mirrors the table in tag_body.md — the
# enum is what makes rejections countable across a run ("how often is the model
# calling things vague?") instead of a pile of free text.
AVOIDED_REASONS = [
    "vague",
    "price_claim",
    "listing_fidelity",
    "sku_spec",
    "no_product_attribute",
    "generic_filler",
    "service_not_product",
    "unsupported",
]

_STRINGS = {"type": "array", "items": {"type": "string"}}


def _usage_keywords() -> dict:
    facets = ("spaces", "placements", "occasions", "used_for")
    return {
        "type": "object",
        "properties": {f: dict(_STRINGS) for f in facets},
        "required": list(facets),
        "additionalProperties": False,
    }


# strict mode requires every property listed in `required` and
# additionalProperties false at every level. That is why nothing here is
# optional: a field with nothing to say comes back as an empty array, which is
# also exactly what we want for a thin review.
SCHEMA = {
    "type": "object",
    "properties": {
        "search_terms": dict(_STRINGS),
        "features": dict(_STRINGS),
        "complaints": dict(_STRINGS),
        "assembly_maintenance": dict(_STRINGS),
        "usage_keywords": _usage_keywords(),
        "avoided": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "phrase": {"type": "string"},
                    "reason": {"type": "string", "enum": AVOIDED_REASONS},
                },
                "required": ["phrase", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "search_terms", "features", "complaints", "assembly_maintenance",
        "usage_keywords", "avoided",
    ],
    "additionalProperties": False,
}

EMPTY_TAGS = {
    "search_terms": [],
    "features": [],
    "complaints": [],
    "assembly_maintenance": [],
    "usage_keywords": {"spaces": [], "placements": [], "occasions": [], "used_for": []},
    "avoided": [],
}


class TaggingError(Exception):
    """A problem that stops the whole run. The message is shown verbatim."""


# ---------------------------------------------------------------------------
# Prompt and client
# ---------------------------------------------------------------------------

def load_prompt() -> tuple[str, str]:
    """The instruction text and a hash of it.

    The hash goes into parse_log.json so that two parse folders can be compared
    and you can tell at a glance whether the prompt changed between them.
    """
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise TaggingError(
            f"The tagging instructions are missing. Expected them at {PROMPT_PATH}."
        ) from None
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def tag_choice() -> dict:
    """The model and effort in force, resolved once so a run stays internally
    consistent even if Settings is saved while it's in flight."""
    return {
        "model": settings.resolve("tag_model"),
        "reasoning": settings.resolve("tag_reasoning"),
    }


_CLIENT = None
_CLIENT_LOCK = threading.Lock()

# Model families differ on whether they accept a reasoning effort. Rather than
# hard-code which, we try it once and remember the answer for the rest of the run.
_SUPPORTS_REASONING = True


def client():
    """One shared client, built on first use so importing this module is cheap."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            if not (config.OPENAI_API_KEY or "").strip():
                raise TaggingError(
                    "No OpenAI key found. Add OPENAI_API_KEY to config.py."
                )
            try:
                from openai import OpenAI
            except ImportError:
                raise TaggingError(
                    "The openai package isn't installed. Run: "
                    "pip install -r requirements.txt"
                ) from None
            _CLIENT = OpenAI(api_key=config.OPENAI_API_KEY)
        return _CLIENT


# ---------------------------------------------------------------------------
# What the model sees
# ---------------------------------------------------------------------------

def build_input(body: dict, product: dict | None) -> str:
    """The user message for one body.

    A listing carries its own title, so it needs no context. A review does: with
    nothing but "Sturdy and it looks great on my mantel" the model cannot tell
    what "it" is. The context block says so explicitly and the prompt forbids
    taking tags from it, because the listing was already tagged separately and
    anything copied across would be counted twice.
    """
    if body.get("type") != "review":
        return f"LISTING\n{body['text']}"

    product = product or {}
    lines = ["CONTEXT (comprehension only — never a source of tags)"]
    if product.get("title"):
        lines.append(f"Product: {product['title']}")
    if product.get("brand"):
        lines.append(f"Brand: {product['brand']}")
    lines.append("")
    lines.append("REVIEW")
    lines.append(body["text"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# One call
# ---------------------------------------------------------------------------

def _status(exc) -> int | None:
    for attr in ("status_code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _fatal(exc, model: str | None = None, what: str = "tagged") -> str | None:
    """A plain-language message if this error means the run cannot continue.

    Retrying a bad key 400 times helps nobody, so authentication and permission
    failures stop everything immediately with something you can act on.

    `model` and `what` are parameters because grouping raises the same errors on a
    different model and a different noun, and two copies of this table would
    inevitably drift apart.
    """
    model = model or settings.resolve("tag_model")
    code = _status(exc)
    name = exc.__class__.__name__
    if code in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return ("OpenAI rejected the API key. Check OPENAI_API_KEY in config.py.")
    if code == 404 or name == "NotFoundError":
        return (
            f'OpenAI does not recognise the model "{model}". '
            f"Pick a different one in Settings."
        )
    if code == 402 or "insufficient_quota" in str(exc):
        return f"This OpenAI account is out of credit, so nothing could be {what}."
    return None


def _retryable(exc) -> bool:
    code = _status(exc)
    if code == 429 or (code is not None and code >= 500):
        return True
    return exc.__class__.__name__ in (
        "RateLimitError", "APITimeoutError", "APIConnectionError",
        "InternalServerError", "APIStatusError",
    )


def _call(prompt: str, user_input: str, choice: dict) -> tuple[dict, dict]:
    """One request. Returns (tags, usage) or raises.

    `choice` is the model and effort resolved once at the top of the run, not read
    here — a settings save mid-run should not change which model half the bodies
    were tagged with, and resolve() touches the disk every time it's called.
    """
    global _SUPPORTS_REASONING

    kwargs = {
        "model": choice["model"],
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "tagged_body",
                "strict": True,
                "schema": SCHEMA,
            }
        },
        "max_output_tokens": config.TAG_MAX_OUTPUT_TOKENS,
    }
    if _SUPPORTS_REASONING and choice["reasoning"]:
        kwargs["reasoning"] = {"effort": choice["reasoning"]}

    try:
        response = client().responses.create(**kwargs)
    except Exception as exc:
        # Some models have no reasoning knob. Find out once, then stop sending it.
        if "reasoning" in kwargs and "reasoning" in str(exc).lower():
            _SUPPORTS_REASONING = False
            kwargs.pop("reasoning")
            response = client().responses.create(**kwargs)
        else:
            raise

    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            raise TaggingError(
                f"The answer was cut off at {config.TAG_MAX_OUTPUT_TOKENS} tokens. "
                f"Raise TAG_MAX_OUTPUT_TOKENS in config.py."
            )
        raise TaggingError(f"OpenAI returned an incomplete answer ({reason}).")

    text = (getattr(response, "output_text", None) or "").strip()
    if not text:
        raise TaggingError("OpenAI returned an empty answer.")

    try:
        tags = json.loads(text)
    except json.JSONDecodeError:
        raise TaggingError("OpenAI returned something that wasn't valid JSON.") from None

    usage = getattr(response, "usage", None)
    return _clean(tags), {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }


def _clean(tags) -> dict:
    """Shape whatever came back into the schema, dropping blanks.

    strict mode makes a malformed response very unlikely, but this is the last
    point where a surprise can be absorbed quietly instead of crashing a run
    that is 300 bodies deep.
    """
    if not isinstance(tags, dict):
        return json.loads(json.dumps(EMPTY_TAGS))

    def strings(value) -> list[str]:
        if not isinstance(value, list):
            return []
        seen, out = set(), []
        for item in value:
            item = item.strip() if isinstance(item, str) else ""
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
        return out

    raw_usage = tags.get("usage_keywords")
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}

    avoided = []
    for entry in tags.get("avoided") or []:
        if not isinstance(entry, dict):
            continue
        phrase = (entry.get("phrase") or "").strip()
        reason = entry.get("reason")
        if phrase:
            avoided.append({
                "phrase": phrase,
                "reason": reason if reason in AVOIDED_REASONS else "vague",
            })

    return {
        "search_terms": strings(tags.get("search_terms")),
        "features": strings(tags.get("features")),
        "complaints": strings(tags.get("complaints")),
        "assembly_maintenance": strings(tags.get("assembly_maintenance")),
        "usage_keywords": {
            facet: strings(raw_usage.get(facet))
            for facet in ("spaces", "placements", "occasions", "used_for")
        },
        "avoided": avoided[:6],
    }


def tag_body(body: dict, product: dict | None, prompt: str,
             choice: dict | None = None) -> dict:
    """Tag one body, retrying the errors worth retrying.

    Never raises for a per-body problem — a failure becomes a record so the other
    399 bodies still land. Only a fatal account-level error propagates.
    """
    choice = choice or tag_choice()
    user_input = build_input(body, product)
    attempts = max(1, config.OPENAI_MAX_RETRIES + 1)
    last = "unknown error"

    for attempt in range(attempts):
        try:
            tags, usage = _call(prompt, user_input, choice)
            return {**body, "tags": tags, "status": "ok", "error": None, "usage": usage}
        except TaggingError as exc:
            last = str(exc)
            break                       # a bad answer, not a flaky connection
        except Exception as exc:
            fatal = _fatal(exc, choice["model"])
            if fatal:
                raise TaggingError(fatal) from exc
            last = str(exc) or exc.__class__.__name__
            if not _retryable(exc) or attempt == attempts - 1:
                break
            # Exponential backoff with jitter, so 8 workers that all hit a rate
            # limit together don't march back in lockstep.
            time.sleep((2 ** attempt) + random.uniform(0, 0.5))

    if _retryable_text(last):
        last = f"Rate limited or unreachable after {attempts} attempts"
    return {**body, "tags": json.loads(json.dumps(EMPTY_TAGS)),
            "status": "failed", "error": last, "usage": {"input_tokens": 0, "output_tokens": 0}}


def _retryable_text(message: str) -> bool:
    lowered = (message or "").lower()
    return "rate limit" in lowered or "timeout" in lowered or "connection" in lowered


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def estimate(bodies: list[dict], prompt: str | None = None) -> dict:
    """What a parse will cost, for the confirm dialog.

    Deliberately rough: four characters to a token, plus a fixed output guess.
    The actuals from response.usage go into parse_log.json after every run, which
    is the number worth trusting.
    """
    if prompt is None:
        prompt, _ = load_prompt()

    taggable, skipped = partition(bodies)
    prompt_tokens = len(prompt) // 4
    input_tokens = sum(prompt_tokens + len(b["text"]) // 4 + 40 for b in taggable)
    output_tokens = len(taggable) * config.TAG_EST_OUTPUT_TOKENS
    model = settings.resolve("tag_model")

    return {
        "bodies_total": len(bodies),
        "to_tag": len(taggable),
        "to_skip": len(skipped),
        "listings": sum(1 for b in taggable if b["type"] == "listing"),
        "reviews": sum(1 for b in taggable if b["type"] == "review"),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        **_price(input_tokens, output_tokens, model),
    }


def _price(input_tokens: int, output_tokens: int, model: str | None = None) -> dict:
    rates = config.MODEL_PRICING.get(model or settings.resolve("tag_model"))
    if not rates:
        return {"cost_usd": None, "cost_known": False}
    cost = (input_tokens / 1e6) * rates["input"] + (output_tokens / 1e6) * rates["output"]
    return {"cost_usd": round(cost, 4), "cost_known": True}


def partition(bodies: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split bodies into the ones worth a call and the ones that aren't.

    Too short to hold a concrete benefit, or the same review text as a body
    already queued for this listing. Pasted reviews are deduped as they arrive,
    but a duplicate can also be typed in by hand on the run page, and tagging
    one twice both costs a call and counts its tags twice in the results. Two
    listings that share a review each keep their own copy — a competitor is
    judged on its own reviews.

    Skipped bodies are copies carrying `skip_reason`, so the caller's dicts stay
    as `build_bodies` made them.
    """
    taggable, skipped = [], []
    seen: dict[str, dict[str, str]] = {}

    for body in bodies:
        text = body.get("text") or ""
        if len(text) < config.MIN_TAG_CHARS:
            skipped.append({**body, "skip_reason": f"under {config.MIN_TAG_CHARS} characters"})
            continue

        if body.get("type") == "review":
            here = seen.setdefault(body.get("asin"), {})
            key = normalize.body_key(text)
            owner, relation = normalize.find_duplicate(key, here)
            if relation:
                skipped.append({**body, "skip_reason": f"duplicate of {owner}"})
                continue
            here[key] = body["body_id"]

        taggable.append(body)

    return taggable, skipped


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run_tagging(job, slug: str, run_id: str) -> dict:
    """Tag every body in a run and write parse-<ts>/.

    Mirrors extract.run_extraction: report progress through `job`, keep going
    past individual failures, and return the summary the UI shows.
    """
    started = time.perf_counter()
    prompt, prompt_sha = load_prompt()

    run = storage.load_run(slug, run_id)
    products = run.get("products", [])
    by_asin = {p.get("asin"): p for p in products}
    bodies = normalize.build_bodies(products)
    if not bodies:
        raise TaggingError(
            "There is nothing to tag in this run — no listing has bullets or "
            "reviews. Add some by hand on the run page, then parse again."
        )

    taggable, skipped = partition(bodies)
    estimated = estimate(bodies, prompt)

    short = [b for b in skipped if not b["skip_reason"].startswith("duplicate")]
    duplicates = len(skipped) - len(short)

    if short:
        job.note(
            f"Skipped {len(short)} {'body' if len(short) == 1 else 'bodies'} "
            f"under {config.MIN_TAG_CHARS} characters — too short to hold a "
            f"concrete benefit. Their text is in parse_log.json."
        )
    if duplicates:
        job.note(
            f"Skipped {duplicates} duplicate review "
            f"{'body' if duplicates == 1 else 'bodies'} — the same text is "
            f"already being tagged under another review on the same listing. "
            f"Tagging it twice would count its tags twice."
        )

    choice = tag_choice()
    job.set_total(len(taggable))
    job.step(label=f"Tagging {len(taggable)} bodies with {choice['model']}…", count=0)

    results: list[dict] = []
    lock = threading.Lock()
    fatal: list[str] = []

    def work(body: dict) -> None:
        if fatal:                       # an account-level error already stopped us
            return
        try:
            result = tag_body(body, by_asin.get(body["asin"]), prompt, choice)
        except TaggingError as exc:
            with lock:
                if not fatal:
                    fatal.append(str(exc))
            return

        with lock:
            results.append(result)
            if result["status"] == "failed":
                job.fail_item(result["body_id"], result["error"])
            job.step(label=f"Tagged {len(results)} of {len(taggable)} bodies")

    workers = max(1, min(config.OPENAI_WORKERS, len(taggable))) if taggable else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, taggable))

    if fatal:
        raise TaggingError(fatal[0])

    # Restore the original body order — the pool returns them as they finish, and
    # a stable file is far easier to diff between two parse runs.
    order = {body["body_id"]: i for i, body in enumerate(bodies)}
    results.sort(key=lambda r: order.get(r["body_id"], 0))

    parse_id, directory = storage.claim_parse_dir(slug, run_id)
    tagged = [r for r in results if r["status"] == "ok"]
    failures = [{"body_id": r["body_id"], "reason": r["error"]}
                for r in results if r["status"] == "failed"]

    storage.write_json(directory / "tagged.json", {
        "parse_id": parse_id,
        "run_id": run_id,
        "slug": slug,
        "generated_at": storage.now_iso(),
        "model": choice["model"],
        "reasoning": choice["reasoning"] if _SUPPORTS_REASONING else None,
        "prompt_sha": prompt_sha,
        "bodies": results,
        "skipped": [{"body_id": b["body_id"], "asin": b["asin"], "type": b["type"],
                     "text": b["text"], "reason": b["skip_reason"]}
                    for b in skipped],
        "failures": failures,
    })

    counts = _counts(tagged)
    usage = {
        "input_tokens": sum(r["usage"]["input_tokens"] for r in results),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in results),
    }
    actual = _price(usage["input_tokens"], usage["output_tokens"], choice["model"])

    if failures:
        job.note(
            f"{len(failures)} of {len(taggable)} bodies could not be tagged. "
            f"They're listed in parse_log.json and can be picked up by parsing again."
        )

    summary = {
        "parse_id": parse_id,
        "run_id": run_id,
        "slug": slug,
        "bodies_total": len(bodies),
        "tagged": len(tagged),
        "skipped": len(skipped),
        "failed": len(failures),
        "empty": sum(1 for r in tagged if not _any_tags(r["tags"])),
        **counts,
        "usage": usage,
        "cost_usd": actual["cost_usd"],
        "estimated_cost_usd": estimated["cost_usd"],
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }

    storage.write_json(directory / "parse_log.json", {
        **summary,
        "generated_at": storage.now_iso(),
        "model": choice["model"],
        "reasoning": choice["reasoning"] if _SUPPORTS_REASONING else None,
        "prompt_sha": prompt_sha,
        "min_tag_chars": config.MIN_TAG_CHARS,
        "estimate": estimated,
        "avoided_reasons": _avoided_tally(tagged),
        "skipped_bodies": [{"body_id": b["body_id"], "reason": b["skip_reason"],
                            "text": b["text"]} for b in skipped],
        "failures": failures,
        "notes": list(job.notes),
    })

    return summary


def _any_tags(tags: dict) -> bool:
    usage = tags.get("usage_keywords") or {}
    return bool(
        tags.get("search_terms") or tags.get("features") or tags.get("complaints")
        or tags.get("assembly_maintenance")
        or any(usage.get(f) for f in ("spaces", "placements", "occasions", "used_for"))
    )


def _counts(tagged: list[dict]) -> dict:
    """Totals for the summary strip, and for the hand-audit in verification."""
    def total(key: str) -> int:
        return sum(len(r["tags"].get(key) or []) for r in tagged)

    usage_total = sum(
        len((r["tags"].get("usage_keywords") or {}).get(facet) or [])
        for r in tagged
        for facet in ("spaces", "placements", "occasions", "used_for")
    )
    return {
        "search_terms_total": total("search_terms"),
        "features_total": total("features"),
        "complaints_total": total("complaints"),
        "assembly_maintenance_total": total("assembly_maintenance"),
        "usage_keywords_total": usage_total,
        "avoided_total": total("avoided"),
    }


def _avoided_tally(tagged: list[dict]) -> dict:
    """How often each rejection reason fired — the prompt-tuning dashboard."""
    tally = {reason: 0 for reason in AVOIDED_REASONS}
    for result in tagged:
        for entry in result["tags"].get("avoided") or []:
            tally[entry["reason"]] = tally.get(entry["reason"], 0) + 1
    return {reason: count for reason, count in tally.items() if count}
