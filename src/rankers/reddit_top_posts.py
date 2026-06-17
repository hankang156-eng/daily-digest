#!/usr/bin/env python3
"""Fetch top posts from selected subreddits for the daily digest.

Reddit blocks unauthenticated `*.json` endpoints, but the Atom RSS feed at
`https://www.reddit.com/r/{sub}/top/.rss?t=day` is still served (HTTP 200)
to a properly-formed User-Agent. We fetch with `requests`, parse with
`feedparser`, and trust Reddit's own ordering of `top`.

Returns article dicts shaped to match the rest of the digest pipeline.
"""

from __future__ import annotations

import calendar
import datetime
import html as html_lib
import re
import time
from typing import Any

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# Reddit gates the project-style UA ("script:foo:v0 (by /u/x)") aggressively for
# unauthenticated RSS in 2026 — even a single request 429s once a UA is throttled.
# Browser-style UA returns 200 reliably for the low daily volume we need (3 reqs/day).
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
REQUEST_TIMEOUT = 10
REQUEST_DELAY_SECONDS = 6.0  # Reddit RSS 429s on bursts; ~6s between subreddit calls clears it
RETRY_ON_429 = 2
RETRY_BACKOFF_SECONDS = 30.0


def _day_bounds_utc(content_date: datetime.date) -> tuple[float, float]:
    start = datetime.datetime(
        content_date.year, content_date.month, content_date.day, tzinfo=datetime.UTC
    )
    end = start + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _time_window(content_date: datetime.date) -> str:
    today_utc = datetime.datetime.now(datetime.UTC).date()
    return "day" if content_date == today_utc - datetime.timedelta(days=1) else "week"


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_selftext(entry: Any) -> str:
    raw = ""
    contents = entry.get("content") if hasattr(entry, "get") else None
    if contents and isinstance(contents, list):
        raw = contents[0].get("value", "") if isinstance(contents[0], dict) else ""
    raw = raw or entry.get("summary", "")
    match = re.search(r'<div class="md">(.*?)</div>', raw, flags=re.DOTALL)
    body = match.group(1) if match else ""
    text = _strip_html(body)
    text = re.sub(r"^\s*submitted by.*$", "", text, flags=re.IGNORECASE).strip()
    return text


def _entry_to_article(entry: Any, subreddit: str) -> dict[str, Any] | None:
    title = (entry.get("title") or "").strip()
    link = entry.get("link") or ""
    if not title or not link:
        return None
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    ts = calendar.timegm(parsed)
    post_date = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).date()
    selftext = _extract_selftext(entry)
    summary = selftext[:300] + ("…" if len(selftext) > 300 else "") if selftext else ""
    author = (entry.get("author") or "").lstrip("/")
    return {
        "title": title,
        "url": link,
        "source": f"r/{subreddit}",
        "outlet": "Reddit",
        "category": "social",
        "date": post_date,
        "summary": summary,
        "reddit_url": link,
        "reddit_author": author,
        "_created_utc": ts,
    }


def _fetch_subreddit_top(
    sess: "requests.Session", subreddit: str, content_date: datetime.date, per_sub: int
) -> list[dict[str, Any]]:
    t = _time_window(content_date)
    url = f"https://www.reddit.com/r/{subreddit}/top/.rss?t={t}&limit=25"
    res = None
    for attempt in range(RETRY_ON_429 + 1):
        try:
            res = sess.get(url, timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            print(f"  [Reddit] r/{subreddit} request failed: {exc}")
            return []
        if res.status_code != 429:
            break
        if attempt < RETRY_ON_429:
            time.sleep(RETRY_BACKOFF_SECONDS)
    if res is None or res.status_code != 200:
        status = res.status_code if res is not None else "no-response"
        print(f"  [Reddit] r/{subreddit} HTTP {status}")
        return []
    feed = feedparser.parse(res.text)
    day_start, day_end = _day_bounds_utc(content_date)
    picked: list[dict[str, Any]] = []
    for entry in feed.entries:
        article = _entry_to_article(entry, subreddit)
        if article is None:
            continue
        ts = article.pop("_created_utc")
        if not (day_start <= ts < day_end):
            continue
        picked.append(article)
        if len(picked) >= per_sub:
            break
    return picked


def fetch_reddit_top_posts(
    content_date: datetime.date,
    subreddits: list[str],
    per_sub: int = 5,
) -> list[dict[str, Any]]:
    if not subreddits:
        return []
    if not HAS_FEEDPARSER or not HAS_REQUESTS:
        print("  [Reddit] Skipped: feedparser/requests not installed")
        return []
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    results: list[dict[str, Any]] = []
    for raw in subreddits:
        sub = (raw or "").strip().lstrip("r/").lstrip("/")
        if not sub:
            continue
        try:
            results.extend(_fetch_subreddit_top(sess, sub, content_date, per_sub))
        except Exception as exc:
            print(f"  [Reddit] r/{sub} unexpected error: {exc}")
        time.sleep(REQUEST_DELAY_SECONDS)
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch top subreddit posts for a date.")
    parser.add_argument("--date", help="Content date YYYY-MM-DD (default: yesterday UTC)")
    parser.add_argument(
        "--subreddits",
        nargs="+",
        default=["ClaudeAI", "ClaudeCode", "ObsidianMD"],
    )
    parser.add_argument("--per-sub", type=int, default=5)
    args = parser.parse_args()

    if args.date:
        target = datetime.date.fromisoformat(args.date)
    else:
        target = datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(days=1)

    posts = fetch_reddit_top_posts(target, args.subreddits, per_sub=args.per_sub)
    print(f"Fetched {len(posts)} posts for {target}")
    for post in posts:
        print(f"  [{post['source']}] {post['date']} | {post['title'][:80]}")
