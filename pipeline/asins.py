"""
ASIN extraction from pasted Amazon URLs.

extract_amazon_asin is carried over unchanged from amazon_web_scrape.py.
parse_input_block wraps it for the UI: it takes the whole pasted block, keeps
each ASIN tied to the line it came from, and reports what it could not read
rather than silently dropping it.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

_BARE_ASIN = re.compile(r"^[A-Z0-9]{10}$", re.IGNORECASE)


def extract_amazon_asin(url: str) -> Optional[str]:
    """
    Extract Amazon ASIN from a URL.

    Args:
        url: Amazon product URL (full or partial)

    Returns:
        10-character ASIN string if found, None otherwise
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()

    # Pattern 1: /dp/{ASIN} - most common
    match = re.search(r'/dp/([A-Z0-9]{10})(?:[/?]|$)', url, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 2: /gp/product/{ASIN} - alternate format
    match = re.search(r'/gp/product/([A-Z0-9]{10})(?:[/?]|$)', url, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 3: /d/{ASIN} - short format
    match = re.search(r'/d/([A-Z0-9]{10})(?:[/?]|$)', url, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 4: Query parameter ?asin={ASIN}
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        if 'asin' in query_params:
            asin = query_params['asin'][0]
            if len(asin) == 10 and re.match(r'^[A-Z0-9]{10}$', asin, re.IGNORECASE):
                return asin.upper()
    except Exception:
        pass

    # Pattern 5: Fallback - any 10-char alphanumeric after /product/
    match = re.search(r'/product/([A-Z0-9]{10})(?:[/?]|$)', url, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 6: Last resort - any standalone 10-char alphanumeric that looks
    # like an ASIN (starts with B0)
    matches = re.findall(r'\b([A-Z0-9]{10})\b', url, re.IGNORECASE)
    for potential_asin in matches:
        if potential_asin.upper().startswith('B0'):
            return potential_asin.upper()

    if matches:
        return matches[0].upper()

    return None


def parse_input_block(text: str) -> dict:
    """
    Turn a pasted block of URLs (or bare ASINs) into a de-duplicated work list.

    Returns:
        {
          "items":      [{"asin": "B08...", "source_url": "<the line>"}],
          "duplicates": [{"asin": "B08...", "source_url": "<the line>"}],
          "unparsed":   ["<line we could not read>"],
          "line_count": <non-empty lines seen>,
        }
    """
    items: list[dict] = []
    duplicates: list[dict] = []
    unparsed: list[str] = []
    seen: set[str] = set()
    line_count = 0

    for raw_line in (text or "").splitlines():
        line = raw_line.strip().strip(",").strip()
        if not line:
            continue
        line_count += 1

        asin = line.upper() if _BARE_ASIN.match(line) else extract_amazon_asin(line)
        if not asin:
            unparsed.append(line)
            continue

        entry = {"asin": asin, "source_url": line}
        if asin in seen:
            duplicates.append(entry)
        else:
            seen.add(asin)
            items.append(entry)

    return {
        "items": items,
        "duplicates": duplicates,
        "unparsed": unparsed,
        "line_count": line_count,
    }
