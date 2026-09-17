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

# One call per tag list, real judgment required — worth the strongest model on the
# Settings menu. Base `gpt-5.6` is deliberately not offered there, so the default
# is the top of the luna/terra/sol family.
OPENAI_GROUP_MODEL = os.getenv("OPENAI_GROUP_MODEL", "gpt-5.6-sol")

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
# Tagging
# ---------------------------------------------------------------------------

# Bodies shorter than this are skipped without a call. "Love it!!" cannot hold a
# concrete benefit, and at ~400 bodies a run the junk adds up.
#
# It is a blunt instrument: "Easy to hang" is a real feature and only 12
# characters. So every skipped body's full text is written to parse_log.json —
# if a genuine feature turns up in that list, lower this and re-parse.
MIN_TAG_CHARS = int(os.getenv("MIN_TAG_CHARS", "30"))

# Reasoning effort for the tagging model. Start low: the prompt carries explicit
# rules and three worked examples, so most of the work is pattern-matching.
# Measured 2026-09-11 over 15 bodies — medium gave identical feature sets on 9 of
# them and the same total count, differing only in word order and where it drew
# merge boundaries, for 21% more output tokens. Low stays.
OPENAI_TAG_REASONING = os.getenv("OPENAI_TAG_REASONING", "low")

# A ceiling, not a target. Tag counts are deliberately uncapped, so this exists
# only to stop a pathological response from running away. A body that hits it is
# recorded as a failure with the reason spelled out, never silently truncated.
TAG_MAX_OUTPUT_TOKENS = int(os.getenv("TAG_MAX_OUTPUT_TOKENS", "4000"))

# Rough output tokens per body, used only for the pre-parse cost estimate. The
# real number is written to parse_log.json after every run — check it there and
# adjust this if the estimate drifts.
TAG_EST_OUTPUT_TOKENS = int(os.getenv("TAG_EST_OUTPUT_TOKENS", "320"))


# ---------------------------------------------------------------------------
# Concurrency and retries
# ---------------------------------------------------------------------------

RAINFOREST_WORKERS = int(os.getenv("RAINFOREST_WORKERS", "5"))
OPENAI_WORKERS = int(os.getenv("OPENAI_WORKERS", "8"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

# A safety valve, not the normal path. Clustering works by comparing every tag
# against every other one, so splitting a list across calls destroys exactly the
# comparisons that matter — two tags in different chunks can never be judged
# together. Measured 2026-09-15: the largest list on disk (olive trees, 290 unique
# features) is ~2,264 tokens, and the 20-ASIN target lands near ~4,700. Everything
# real fits in one call, so this only engages if a list is pathologically large.
GROUP_CHUNK_SIZE = int(os.getenv("GROUP_CHUNK_SIZE", "1200"))

# Reasoning effort for the grouping model. Higher than tagging's `low` because
# this step is judgment, not pattern-matching: deciding that "sturdy bowl" and
# "thick build" are one customer concern while "real glass front" and "clear,
# vivid viewing" are two is the whole job.
OPENAI_GROUP_REASONING = os.getenv("OPENAI_GROUP_REASONING", "medium")

# A ceiling, not a target. A 290-tag list returning ~36 clusters with titles,
# descriptions and indices runs about 2,400 output tokens; this leaves room for a
# much larger category without letting a runaway response bill forever.
GROUP_MAX_OUTPUT_TOKENS = int(os.getenv("GROUP_MAX_OUTPUT_TOKENS", "8000"))

# Rough output tokens per list, for the pre-grouping cost estimate. Actuals land
# in cluster_log.json after every run — check there and adjust if this drifts.
GROUP_EST_OUTPUT_TOKENS = int(os.getenv("GROUP_EST_OUTPUT_TOKENS", "2400"))


# ---------------------------------------------------------------------------
# Themes — a second, smaller call per list that groups clusters into themes
# ---------------------------------------------------------------------------

# Build themes right after tag clustering. Off means the user runs "Group into
# themes" from the results view instead. Overridable from Settings.
AUTO_THEMES = os.getenv("AUTO_THEMES", "1") not in ("0", "false", "False")

# A list needs at least this many clusters before it is grouped into themes.
# Twelve or fewer clusters read fine as a flat grid, so they are left alone.
THEME_MIN_CLUSTERS = int(os.getenv("THEME_MIN_CLUSTERS", "13"))

# A ceiling for one theme call. A 57-cluster list returns ~9 themes with titles,
# summaries and indices in well under 1,000 tokens.
THEME_MAX_OUTPUT_TOKENS = int(os.getenv("THEME_MAX_OUTPUT_TOKENS", "4000"))

# Rough output tokens per list, for the pre-theming cost estimate.
THEME_EST_OUTPUT_TOKENS = int(os.getenv("THEME_EST_OUTPUT_TOKENS", "900"))


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(APP_ROOT / "data")))

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))
OPEN_BROWSER = os.getenv("OPEN_BROWSER", "1") == "1"
