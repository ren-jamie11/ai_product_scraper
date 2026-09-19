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
    # Only used to partition long tag lists before clustering (see GROUP_EMBED_MODEL).
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
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

# Amazon's product page cuts long reviews off at "Read more", so the same review
# copied from there and from the reviews page differs only by its tail. A body
# that is a prefix of another counts as the same review once both are at least
# this long; below it only an exact match counts, so two short reviews that open
# the same way ("Beautiful frame, ..." ) can't collide.
DUP_PREFIX_MIN_CHARS = int(os.getenv("DUP_PREFIX_MIN_CHARS", "60"))


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

# Seconds to wait for one grouping or theme call. The SDK's default is 600, and
# the grouping model writes ~60-70 tokens a second (measured 2026-09-18 across
# twelve parses), so a list past ~1,300 tags would time out at the default and
# then be retried twice — paying three times for nothing. Tagging calls are
# short and keep the SDK default.
OPENAI_LONG_CALL_TIMEOUT = int(os.getenv("OPENAI_LONG_CALL_TIMEOUT", "1800"))


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

# Lists at or under this many unique tags are clustered in one call, exactly as
# they always were: the model sees every tag at once and judges every pair
# together. Above it the list is partitioned by meaning (pipeline/partition.py),
# each partition is clustered in parallel by the same rules, and the partial
# clusters are joined by one merge call. Measured 2026-09-18: a single call costs
# ~0.4 s per tag and hits the SDK read timeout near 1,300 tags, and every list
# validated by hand so far was 75-290 tags — the regime the partitions stay in.
GROUP_SINGLE_CALL_MAX = int(os.getenv("GROUP_SINGLE_CALL_MAX", "350"))

# Tags per partition, and the size below which a partition is folded into its
# nearest neighbour rather than clustered on its own.
GROUP_PARTITION_TARGET = int(os.getenv("GROUP_PARTITION_TARGET", "250"))
GROUP_PARTITION_MIN = int(os.getenv("GROUP_PARTITION_MIN", "40"))

# Embeddings decide which tags share a partition. 256 dimensions is plenty for a
# few thousand short phrases and keeps the bisection instant.
GROUP_EMBED_MODEL = os.getenv("GROUP_EMBED_MODEL", "text-embedding-3-small")
GROUP_EMBED_DIMENSIONS = int(os.getenv("GROUP_EMBED_DIMENSIONS", "256"))

# The merge call joins partial clusters that mean the same thing. It is a
# cluster-level judgement — the kind the theme step makes — and that step measured
# `medium` as unstable and `high` as consistent (2026-09-17), so this is pinned
# rather than following the Settings dropdown.
GROUP_MERGE_REASONING = os.getenv("GROUP_MERGE_REASONING", "high")
GROUP_MERGE_MAX_OUTPUT_TOKENS = int(os.getenv("GROUP_MERGE_MAX_OUTPUT_TOKENS", "24000"))

# Output tokens for the merge call in the cost estimate: a reasoning allowance
# plus a little per cluster it has to place. Unmeasured until the first
# partitioned run — compare with cluster_log.json afterwards and adjust.
GROUP_MERGE_EST_OUTPUT_BASE = int(os.getenv("GROUP_MERGE_EST_OUTPUT_BASE", "6000"))
GROUP_MERGE_EST_OUTPUT_PER_CLUSTER = int(os.getenv("GROUP_MERGE_EST_OUTPUT_PER_CLUSTER", "40"))

# Reasoning effort for the grouping model. Higher than tagging's `low` because
# this step is judgment, not pattern-matching: deciding that "sturdy bowl" and
# "thick build" are one customer concern while "real glass front" and "clear,
# vivid viewing" are two is the whole job.
OPENAI_GROUP_REASONING = os.getenv("OPENAI_GROUP_REASONING", "medium")

# A ceiling, not a target. Reasoning tokens count against it, and they are ~70%
# of what a grouping call produces. Measured 2026-09-18 over every grouped parse
# on disk: about 25-28 output tokens per unique tag at `medium`, so a 494-tag
# list needs ~14,000 and a 1,000-tag list ~27,000. The old 8,000 ceiling is what
# cut off dishwasher-rack. 40,000 covers ~1,400 tags in one call; above that the
# partitioned path (GROUP_SINGLE_CALL_MAX) keeps every call well under it.
GROUP_MAX_OUTPUT_TOKENS = int(os.getenv("GROUP_MAX_OUTPUT_TOKENS", "40000"))

# Output tokens per list for the pre-grouping cost estimate: base + per-tag. Fit to
# the same measurement (olive trees 283 tags → 7,852 actual vs 7,875 estimated;
# acacia riser 267 → 7,143 vs 7,475). Actuals land in cluster_log.json after every
# run — check there and adjust if this drifts.
GROUP_EST_OUTPUT_BASE = int(os.getenv("GROUP_EST_OUTPUT_BASE", "800"))
GROUP_EST_OUTPUT_PER_TAG = int(os.getenv("GROUP_EST_OUTPUT_PER_TAG", "25"))

# How fast the grouping model writes, used only to tell you how long a list will
# take while its progress bar sits still. Measured 60-70 tokens/second on
# gpt-5.6-sol; adjust if a different model is noticeably faster or slower.
GROUP_TOKENS_PER_SEC = int(os.getenv("GROUP_TOKENS_PER_SEC", "60"))


# ---------------------------------------------------------------------------
# Themes — a second, smaller call per list that groups clusters into themes
# ---------------------------------------------------------------------------

# Build themes right after tag clustering. Off means the user runs "Group into
# themes" from the results view instead. Overridable from Settings.
AUTO_THEMES = os.getenv("AUTO_THEMES", "1") not in ("0", "false", "False")

# A list needs at least this many clusters before it is grouped into themes.
# Twelve or fewer clusters read fine as a flat grid, so they are left alone.
THEME_MIN_CLUSTERS = int(os.getenv("THEME_MIN_CLUSTERS", "13"))

# A ceiling for one theme call. The visible answer (~9 themes with titles, summaries
# and indices) is well under 1,000 tokens, but the model's reasoning tokens count
# against this limit too. Measured 2026-09-17 on a 35-cluster list: ~2,500-3,500
# output tokens at `medium`, and `high` overran a 4,000 ceiling; a 57-cluster list
# used 7,300 at `high` (2026-09-18). A 50-ASIN category can reach 100-150 feature
# clusters, so 24,000 leaves room without letting a runaway response bill forever.
THEME_MAX_OUTPUT_TOKENS = int(os.getenv("THEME_MAX_OUTPUT_TOKENS", "24000"))

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
