#!/usr/bin/env python3
"""Fetch top posts from selected subreddits for the daily digest.

Reddit blocks unauthenticated `*.json` endpoints in 2026, but the Atom
RSS feed at `https://www.reddit.com/r/{sub}/top/.rss?t=week` is still
served (HTTP 200) to a browser-style User-Agent. We fetch with
`requests`, parse with `feedparser`, and trust Reddit's own ranking.

For r/ClaudeAI posts only, also fetches the post's comments RSS and
captures the body of any comment authored by /u/ClaudeAI-mod-bot
(treated as the pinned bot note). Comments RSS exposes author + body
but NOT the `stickied` flag — author match is the proxy.

RSS does NOT expose score or num_comments — those fields are absent
from articles produced here.

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


# Reddit gates project-style UAs ("script:foo:v0 (by /u/x)") aggressively for
# unauthenticated RSS in 2026 — browser-style UA returns 200 reliably for the
# low daily volume here (~13 reqs/day with mod-bot fetch).
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
REQUEST_TIMEOUT = 10
REQUEST_DELAY_SECONDS = 6.0  # Reddit RSS 429s on bursts
RETRY_ON_429 = 2
RETRY_BACKOFF_SECONDS = 30.0

MOD_BOT_SUBREDDIT = "ClaudeAI"
MOD_BOT_AUTHOR = "/u/ClaudeAI-mod-bot"
COMMENT_FETCH_LIMIT = 50


def _day_bounds_utc(content_date: datetime.date) -> tuple[float, float]:
    start = datetime.datetime(
        content_date.year, content_date.month, content_date.day, tzinfo=datetime.UTC
    )
    end = start + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _time_window(content_date: datetime.date) -> str:
    today_utc = datetime.datetime.now(datetime.UTC).date()
    diff_days = (today_utc - content_date).days
    if diff_days <= 7:
        return "week"
    if diff_days <= 31:
        return "month"
    return "year"


def _http_get_rss(sess: "requests.Session", url: str) -> str:
    res = None
    for attempt in range(RETRY_ON_429 + 1):
        try:
            res = sess.get(url, timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            print(f"  [Reddit] request failed: {exc}")
            return ""
        if res.status_code != 429:
            break
        if attempt < RETRY_ON_429:
            time.sleep(RETRY_BACKOFF_SECONDS)
    if res is None or res.status_code != 200:
        status = res.status_code if res is not None else "no-response"
        print(f"  [Reddit] HTTP {status} for {url}")
        return ""
    return res.text


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_md_body(raw: str) -> str:
    """Atom comment/post entries embed body inside `<div class="md">...</div>`."""
    raw = html_lib.unescape(raw or "")
    match = re.search(r'<div class="md">(.*?)</div>', raw, flags=re.DOTALL)
    body = match.group(1) if match else raw
    text = _strip_html(body)
    text = re.sub(r"^\s*submitted by.*$", "", text, flags=re.IGNORECASE).strip()
    return text


def _fetch_mod_bot_tldr(sess: "requests.Session", subreddit: str, post_id: str) -> str:
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/.rss?sort=top&limit={COMMENT_FETCH_LIMIT}"
    body = _http_get_rss(sess, url)
    if not body:
        return ""
    feed = feedparser.parse(body)
    for entry in feed.entries:
        author = (entry.get("author") or "").strip()
        if author != MOD_BOT_AUTHOR:
            continue
        raw = ""
        contents = entry.get("content")
        if contents and isinstance(contents, list) and isinstance(contents[0], dict):
            raw = contents[0].get("value", "")
        raw = raw or entry.get("summary", "")
        return _extract_md_body(raw)
    return ""


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
    raw = ""
    contents = entry.get("content")
    if contents and isinstance(contents, list) and isinstance(contents[0], dict):
        raw = contents[0].get("value", "")
    selftext = _extract_md_body(raw or entry.get("summary", ""))
    summary = selftext[:300] + ("…" if len(selftext) > 300 else "") if selftext else ""
    author = (entry.get("author") or "").lstrip("/")
    post_id = (entry.get("id") or "").removeprefix("t3_")
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
        "reddit_id": post_id,
        "_created_utc": ts,
    }


def _fetch_subreddit_top(
    sess: "requests.Session",
    subreddit: str,
    content_date: datetime.date,
    per_sub: int,
) -> list[dict[str, Any]]:
    t = _time_window(content_date)
    url = f"https://www.reddit.com/r/{subreddit}/top/.rss?t={t}&limit=100"
    body = _http_get_rss(sess, url)
    if not body:
        return []
    feed = feedparser.parse(body)
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

    if subreddit == MOD_BOT_SUBREDDIT:
        for article in picked:
            post_id = article.get("reddit_id")
            if not post_id:
                continue
            time.sleep(REQUEST_DELAY_SECONDS)
            article["bot_tldr"] = _fetch_mod_bot_tldr(sess, subreddit, post_id)
    return picked


def fetch_reddit_top_posts(
    content_date: datetime.date,
    subreddit_counts: dict[str, int],
) -> list[dict[str, Any]]:
    if not subreddit_counts:
        return []
    if not HAS_FEEDPARSER or not HAS_REQUESTS:
        print("  [Reddit] Skipped: feedparser/requests not installed")
        return []
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    results: list[dict[str, Any]] = []
    for raw_sub, count in subreddit_counts.items():
        sub = (raw_sub or "").strip().lstrip("r/").lstrip("/")
        if not sub:
            continue
        try:
            per_sub = max(1, int(count))
        except (TypeError, ValueError):
            per_sub = 5
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
        default=["ClaudeAI:10", "ClaudeCode:5", "ObsidianMD:5"],
        help="Subreddit:count pairs",
    )
    args = parser.parse_args()

    if args.date:
        target = datetime.date.fromisoformat(args.date)
    else:
        target = datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(days=1)

    counts: dict[str, int] = {}
    for raw in args.subreddits:
        name, _, n = raw.partition(":")
        counts[name] = int(n) if n else 5

    posts = fetch_reddit_top_posts(target, counts)
    print(f"Fetched {len(posts)} posts for {target}")
    for post in posts:
        bot = " [BOT-TLDR]" if post.get("bot_tldr") else ""
        print(f"  [{post['source']}] {post['date']} | {post['title'][:70]}{bot}")
