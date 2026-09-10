"""
Configuration for the Competitor Feature-Benefit Extractor.

Template. Copy to config.py (which is git-ignored) and fill in the keys.
config.py is the one file you edit by hand. Every value can also be supplied as an
environment variable of the same name, which wins over what is written here.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

# Rainforest API — https://www.rainforestapi.com/  (used to pull listing + review data)
RAINFOREST_API_KEY = os.getenv("RAINFOREST_API_KEY", "")

# OpenAI — https://platform.openai.com/api-keys  (used to tag and group features)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# One call per text body, high volume — a mid tier is the right trade here.
OPENAI_TAG_MODEL = os.getenv("OPENAI_TAG_MODEL", "gpt-5.6-terra")

# Few calls, real judgment required — worth the frontier model.
OPENAI_GROUP_MODEL = os.getenv("OPENAI_GROUP_MODEL", "gpt-5.6")

# USD per 1,000,000 tokens, used only for the cost estimate shown before a parse.
# VERIFY these against https://openai.com/api/pricing — they are placeholders.
MODEL_PRICING = {
    "gpt-5.6":       {"input": 1.25, "output": 10.00},
    "gpt-5.6-sol":   {"input": 1.25, "output": 10.00},
    "gpt-5.6-terra": {"input": 0.25, "output": 2.00},
    "gpt-5.6-luna":  {"input": 0.05, "output": 0.40},
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

AMAZON_DOMAIN = os.getenv("AMAZON_DOMAIN", "amazon.com")

# How many reviews to try to collect per ASIN.
#
# Reality check (probe_reviews.py, 2026-09-10): Rainforest's `type=reviews`
# request answers HTTP 503 "temporarily unavailable", so the only reviews we can
# get are the ~9 that come free with each product call. This target is an upper
# bound, not a promise — the run stops as soon as the API has no more to give.
# Re-run probe_reviews.py now and then to see whether the endpoint has returned.
REVIEW_TARGET = int(os.getenv("REVIEW_TARGET", "30"))

# Whether to try the extra reviews endpoint at all. Checked once per run, not
# once per ASIN. Leave it on: the failed check is free and costs one round trip.
TRY_REVIEWS_ENDPOINT = os.getenv("TRY_REVIEWS_ENDPOINT", "1") == "1"

# Rainforest's product call includes `top_reviews` only intermittently. Measured
# 2026-09-10 over 8 calls to one ASIN: reviews arrived 5 times, i.e. a ~62% hit
# rate, and every hit returned the identical review set. So retrying is the only
# way to get dependable coverage, and there is nothing to gain past the first
# hit. At 62%, 3 attempts cover ~94% of listings and 4 cover ~98%, for an
# average of ~1.6 credits per ASIN.
#
# We only retry when it can help: the listing has ratings but this response had
# no reviews. Dead ASINs and genuinely review-less listings stop after one call.
PRODUCT_ATTEMPTS = int(os.getenv("PRODUCT_ATTEMPTS", "4"))

# Reviews shorter than this yield no concrete benefit and are not worth a call.
MIN_REVIEW_CHARS = int(os.getenv("MIN_REVIEW_CHARS", "1"))


# ---------------------------------------------------------------------------
# Concurrency and retries
# ---------------------------------------------------------------------------

RAINFOREST_WORKERS = int(os.getenv("RAINFOREST_WORKERS", "5"))
OPENAI_WORKERS = int(os.getenv("OPENAI_WORKERS", "8"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

# Unique tags sent to the model in one grouping call.
GROUP_CHUNK_SIZE = int(os.getenv("GROUP_CHUNK_SIZE", "150"))


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(APP_ROOT / "data")))

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))
OPEN_BROWSER = os.getenv("OPEN_BROWSER", "1") == "1"
