"""
Reviews straight from Amazon's review pages.

Rainforest stopped returning reviews in November 2026, so they come from Amazon
itself, through the saved login in `amazon_cookies.<site>.txt`. The fetching and
parsing are `amazon_reviews_new.py`'s (repo root, imported untouched); this module
adds the three things the app needs on top of it:

  * A sample balanced across star ratings. Low-star reviews are where the
    product's problems are, and a plain "top reviews" page is mostly five stars,
    so reviews are taken ten at a time from 1 star up to 5 stars, round and round,
    until the cap is reached or every rating runs dry.
  * No surprises. The script's own entry point opens a browser to sign in when
    the cookie has lapsed; here that is an exception the caller reports, and the
    sign-in window opens only when the user asks for it.
  * The app's review shape, so everything downstream reads these exactly like
    the reviews Rainforest used to send.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import amazon_reviews_new as scraper

import config
from pipeline import normalize

ROOT = Path(__file__).resolve().parent.parent

# Lowest first: when the cap cuts a round short, it is the high ratings that lose.
STARS = ("one_star", "two_star", "three_star", "four_star", "five_star")
STAR_VALUE = {name: i + 1 for i, name in enumerate(STARS)}

# A cap of 100 with one well-stocked rating needs 10 pages of it; nothing sane needs more.
MAX_PAGES_PER_STAR = 12


class SignInNeeded(Exception):
    """No usable login for this site. Nothing more can be fetched until the user signs in."""


class RobotCheck(Exception):
    """Amazon answered with a robot check or a block. Stop, and try again later."""


class ReviewError(Exception):
    """This ASIN's reviews couldn't be read. Other ASINs may still work."""


def site_tld(domain: str) -> str:
    """`amazon.ca` -> `ca`, `amazon.co.uk` -> `co.uk`."""
    return domain.split("amazon.", 1)[-1].strip(".").lower()


def cookies_path(domain: str) -> Path:
    return ROOT / f"amazon_cookies.{site_tld(domain)}.txt"


def signed_in(domain: str) -> bool:
    """Whether a login is saved for this site. It may still have expired."""
    return "=" in scraper.read_cookie_line(cookies_path(domain))


def sign_in_message(domain: str) -> str:
    return (f"Amazon sign-in for {domain} is missing or has expired, so reviews "
            f"weren't fetched. Sign in, then press Fetch reviews.")


class Session:
    """One signed-in connection to one Amazon site, shared by every ASIN in a run."""

    def __init__(self, domain: str):
        self.domain = domain
        self.tld = site_tld(domain)
        self.path = cookies_path(domain)
        cookies = scraper.read_cookie_line(self.path)
        if "=" not in cookies:
            raise SignInNeeded(sign_in_message(domain))
        self.client = scraper.DirectClient(cookies, self.tld, delay=config.AMAZON_DELAY,
                                           cookies_path=self.path)
        self.healthy = True

    @property
    def requests(self) -> int:
        return self.client.requests_made

    def close(self) -> None:
        # Amazon refreshes the login as it is used; saving it back keeps it alive.
        # Not after a sign-in failure, which would save the signed-out state.
        if self.healthy:
            self.client.persist_cookies()


class _StarCursor:
    """Pages through one star rating's reviews, ten at a time."""

    def __init__(self, session: Session, asin: str, star: str, sort: str):
        self.session, self.asin, self.star, self.sort = session, asin, star, sort
        self.host = f"www.amazon.{session.tld}"
        self.page = 0
        self.token = None
        self.csrf = ""
        self.done = False
        self.error = None
        self.summary = {}
        self.first_url = (
            f"https://{self.host}/product-reviews/{asin}/?ie=UTF8&pageNumber=1"
            f"&filterByStar={star}&reviewerType=all_reviews&sortBy={sort}"
        )

    def _check(self, status: str) -> None:
        if status in (scraper.PageStatus.SIGNIN, scraper.PageStatus.BLOCKED):
            self.session.healthy = False
            raise SignInNeeded(sign_in_message(self.session.domain))
        if status == scraper.PageStatus.CAPTCHA:
            raise RobotCheck("Amazon asked for a robot check.")

    def _get(self, fn, *args, **kwargs) -> str:
        try:
            return fn(*args, **kwargs)
        except RuntimeError as exc:
            text = str(exc)
            if "404" in text:
                raise ReviewError(f"{self.asin} has no review page on {self.session.domain}.")
            if "403" in text or "405" in text:
                raise RobotCheck("Amazon blocked the request.")
            raise ReviewError(f"Amazon didn't answer for {self.asin} ({text[:120]}).")

    def next_page(self) -> list[dict]:
        """The next ten or so reviews; an empty list once this rating is exhausted."""
        if self.done:
            return []
        self.page += 1

        if self.page == 1:
            html = self._get(self.session.client.get, self.first_url)
            result = scraper.parse_reviews_page(html, self.host)
            self._check(result["status"])
            if result["status"] == scraper.PageStatus.UNEXPECTED:
                raise ReviewError(f"Amazon's review page for {self.asin} wasn't recognisable.")
            rows = result["reviews"]
            self.summary = {k: v for k, v in (result.get("summary") or {}).items() if v}
            self.csrf = result["csrf"]
            self.token = (result["next_state"] or {}).get("nextPageToken")
        else:
            data = {
                "asin": self.asin, "pageNumber": str(self.page), "nextPageToken": self.token,
                "filterByStar": self.star, "reviewerType": "all_reviews", "sortBy": self.sort,
                "formatType": "", "filterByKeyword": "", "filterByLanguage": "",
                "mediaType": "all_contents", "csrf": self.csrf,
            }
            headers = {"Referer": self.first_url, "Origin": f"https://{self.host}",
                       "anti-csrftoken-a2z": self.csrf}
            body = self._get(self.session.client.post_form,
                             f"https://{self.host}{scraper.AJAX_REVIEWS_PATH}",
                             data=data, headers=headers)
            self._check(scraper.detect_status(body))
            result = scraper.parse_ajax_body(body, self.host)
            if not result["recognized"]:
                raise ReviewError(f"Amazon's next page of reviews for {self.asin} wasn't recognisable.")
            rows = result["rows"]
            previous, self.token = self.token, result["token"]
            if self.token == previous:
                self.token = None

        if not rows or not self.token or self.page >= MAX_PAGES_PER_STAR:
            self.done = True
        for row in rows:
            row.update({"filter": self.star, "page": self.page})
        return rows


def fetch_reviews(session: Session, asin: str, cap: int, sort: str | None = None) -> dict:
    """
    Up to `cap` reviews for one ASIN, balanced across star ratings.

    Raises SignInNeeded or RobotCheck when nothing further can be fetched for
    any ASIN, and ReviewError when only this one failed outright. A rating that
    fails part-way is recorded and the others carry on.

    Returns the raw payload that is written to disk: the scraper's own rows
    under `reviews`, plus how each rating went.
    """
    sort = sort or config.AMAZON_REVIEW_SORT
    before = session.requests
    cursors = [_StarCursor(session, asin, star, sort) for star in STARS]
    rows: list[dict] = []
    seen: set[str] = set()
    summary: dict = {}
    reached = 0

    while len(rows) < cap and any(not c.done for c in cursors):
        for cursor in cursors:
            if cursor.done or len(rows) >= cap:
                continue
            try:
                page = cursor.next_page()
                reached += 1
            except ReviewError as exc:
                # The first request failing means the ASIN itself is the problem.
                if not reached:
                    raise
                cursor.done, cursor.error = True, str(exc)
                continue
            summary = summary or cursor.summary
            for row in page:
                key = scraper.review_identity(row)
                if key in seen or len(rows) >= cap:
                    continue
                seen.add(key)
                rows.append(row)

    return {
        "asin": asin,
        "domain": session.domain,
        "sort": sort,
        "cap": cap,
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "requests": session.requests - before,
        "stars": [{"filter": c.star, "pages": c.page, "exhausted": c.done and not c.error,
                   "fetched": sum(1 for r in rows if r.get("filter") == c.star),
                   "error": c.error} for c in cursors],
        **summary,
        "review_count": len(rows),
        "reviews": rows,
    }


def to_review(row: dict) -> dict | None:
    """One scraped row in the app's review shape, or None if it has no usable text."""
    body = normalize.clean_text(row.get("review"))
    if len(body) < config.MIN_REVIEW_CHARS:
        return None
    stars = row.get("stars")
    if stars is None:
        stars = STAR_VALUE.get(row.get("filter"))
    elif float(stars).is_integer():
        stars = int(stars)
    return {
        "id": row.get("review_id") or None,
        "title": normalize.clean_text(row.get("title")),
        "body": body,
        "rating": stars,
        "date": normalize._parse_date(row.get("date") or ""),
        "verified_purchase": bool(row.get("verified_purchase")),
        "helpful_votes": row.get("helpful_votes") or 0,
        "source": "amazon",
    }


def to_reviews(payload: dict) -> list[dict]:
    """Every usable review in a raw payload, ids and bodies deduplicated."""
    reviews, ids, bodies = [], set(), set()
    for row in (payload or {}).get("reviews") or []:
        review = to_review(row)
        if not review:
            continue
        key = normalize.body_key(review["body"])
        if (review["id"] and review["id"] in ids) or key in bodies:
            continue
        if not review["id"]:
            review["id"] = f"amazon_{len(reviews) + 1}"
        ids.add(review["id"])
        bodies.add(key)
        reviews.append(review)
    return reviews


# One sign-in window at a time; a second click while it is open is refused.
_LOGIN_LOCK = threading.Lock()


def sign_in(domain: str, log=None, wait_seconds: int = 300) -> None:
    """Open a browser window on Amazon's sign-in page and save the login once it's done."""
    if not _LOGIN_LOCK.acquire(blocking=False):
        raise RuntimeError("A sign-in window is already open.")
    try:
        try:
            scraper.interactive_login(site_tld(domain), cookies_path(domain),
                                      log or (lambda *_: None), wait_seconds=wait_seconds)
        except SystemExit as exc:      # the script's way of saying playwright is missing
            raise RuntimeError(
                "The sign-in window needs Playwright. In the project's .venv run: "
                "pip install playwright && playwright install chromium") from exc
        if not signed_in(domain):
            raise RuntimeError(f"Sign-in to {domain} didn't complete.")
    finally:
        _LOGIN_LOCK.release()
