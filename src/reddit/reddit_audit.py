#!/usr/bin/env python3
"""
Reddit subscription and saved-post audit.

Reads the local Reddit export files in _inputs/ and writes analysis artifacts to output/.
Network enrichment uses Reddit's public JSON endpoints and is intentionally cache-backed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests


SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parents[1]
INPUT_DIR = REPO_ROOT / "data" / "reddit" / "audit_inputs"
OUTPUT_DIR = REPO_ROOT / "data" / "reddit" / "audit_outputs"
CACHE_DIR = OUTPUT_DIR

SUBSCRIPTIONS_FILE = INPUT_DIR / "2026.05.02_subscribed_subreddits.csv"
SAVED_CSV_FILE = INPUT_DIR / "2026.04.03_saved_posts.csv"
SAVED_JSON_FILE = INPUT_DIR / "2026.05.02_saved_posts.json"

USER_AGENT = "script:daily-digest-cleanup:v0.1.0 (by /u/miss_comte)"
RATE_LIMIT_FLOOR = 10.0
RATE_LIMIT_PAUSE_SECONDS = 2.0
REQUEST_DELAY_SECONDS = 0.03


CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI, LLMs & Automation", ("ai", "artificial", "chatgpt", "openai", "anthropic", "claude", "codex", "agent", "machinelearning", "singularity", "localllama", "prompt")),
    ("Programming, Data & Engineering", ("programming", "coding", "python", "javascript", "webdev", "data", "datasets", "selfhosted", "obsidian", "software", "devops", "sysadmin", "datascience", "analytics")),
    ("Business, Careers & Work", ("mba", "career", "jobs", "product", "consulting", "finance", "financial", "wallstreet", "pm", "strategy", "management", "linkedin", "overemployed")),
    ("News, Society & Politics", ("news", "politics", "world", "economics", "law", "collapse", "dystopia", "aboringdystopia", "ukraine", "china", "nyc")),
    ("Music, Production & Nightlife", ("music", "edm", "techno", "house", "jazz", "ableton", "synth", "dj", "aves", "rave", "overload", "hiphop", "kpop", "burial", "listentothis")),
    ("Creative Making, Design & Fashion", ("design", "fashion", "handbag", "sewing", "craft", "clay", "polymer", "photography", "35mm", "interiordesign", "architecture", "repurposed")),
    ("Food, Travel & Local Life", ("food", "cooking", "travel", "visitors", "asknyc", "nyc", "hawaii", "cocktails", "coffee", "restaurant")),
    ("Lifestyle, Health & Self-Management", ("adhd", "adulting", "productivity", "fitness", "health", "skincare", "relationship", "heartbreak", "lifeprotips", "youshouldknow")),
    ("Entertainment, Books & Media", ("books", "movies", "television", "netflix", "gaming", "streaming", "anime", "popculture", "funny")),
    ("Learning, Science & Puzzles", ("math", "science", "askscience", "puzzles", "crossword", "history", "linguistics", "learn")),
]

DIGEST_SIGNALS = (
    "technology",
    "anthropic",
    "claude",
    "codex",
    "openai",
    "chatgpt",
    "localllama",
    "machinelearning",
    "programming",
    "selfhosted",
    "datasets",
    "productmanagement",
    "theoverload",
    "music",
    "jazz",
    "electronicmusic",
    "mba",
    "financialcareers",
    "obsidian",
)

FEED_GROUP_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI Workbench", ("ai", "chatgpt", "openai", "anthropic", "claude", "codex", "agent", "localllama", "prompt", "machinelearning")),
    ("Technology & Programming", ("technology", "programming", "coding", "python", "selfhosted", "datasets", "obsidian", "software", "webdev")),
    ("Career, Business & Markets", ("mba", "career", "jobs", "product", "consulting", "finance", "financial", "wallstreet", "management")),
    ("Music Discovery", ("music", "edm", "techno", "house", "jazz", "ableton", "synth", "overload", "burial", "electronic")),
    ("NYC, Food & Travel", ("nyc", "food", "cooking", "travel", "visitors", "hawaii", "cocktails", "restaurant")),
    ("Making, Design & Style", ("design", "fashion", "handbag", "sewing", "craft", "clay", "photography", "architecture")),
]

LOW_SIGNAL_DEFAULTS = (
    "funny",
    "beamazed",
    "askreddit",
    "amitheasshole",
    "aitah",
    "aita",
    "pics",
    "memes",
    "gaming",
    "netflixbestof",
    "streamingbestof",
)

SHORT_TOKEN_NEEDLES = {"ai", "pm", "dj"}


def read_subscriptions() -> list[str]:
    with SUBSCRIPTIONS_FILE.open(encoding="utf-8-sig", newline="") as f:
        return [row["current"].strip() for row in csv.DictReader(f) if row.get("current", "").strip()]


def parse_saved_csv() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with SAVED_CSV_FILE.open(encoding="utf-8-sig", newline="") as f:
        for idx, row in enumerate(csv.DictReader(f), start=1):
            url = row.get("url", "").strip()
            match = re.search(r"reddit\.com/r/([^/]+)/comments/([^/]+)", url)
            subreddit = match.group(1) if match else ""
            post_id = match.group(2) if match else row.get("title", "").strip()
            rows.append(
                {
                    "source_file": SAVED_CSV_FILE.name,
                    "source_order": idx,
                    "id": post_id,
                    "subreddit": subreddit,
                    "title": "",
                    "url": url,
                    "created_utc": None,
                    "score": None,
                    "num_comments": None,
                    "domain": "",
                    "post_hint": "",
                }
            )
    return rows


def parse_saved_json() -> list[dict[str, Any]]:
    data = json.loads(SAVED_JSON_FILE.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for idx, child in enumerate(data.get("data", {}).get("children", []), start=1):
        post = child.get("data", {})
        rows.append(
            {
                "source_file": SAVED_JSON_FILE.name,
                "source_order": idx,
                "id": post.get("id", ""),
                "subreddit": post.get("subreddit", ""),
                "title": post.get("title", ""),
                "url": "https://www.reddit.com" + post.get("permalink", ""),
                "created_utc": post.get("created_utc"),
                "score": post.get("score"),
                "num_comments": post.get("num_comments"),
                "domain": post.get("domain", ""),
                "post_hint": post.get("post_hint", ""),
            }
        )
    return rows


def classify_text(*parts: str) -> str:
    text = " ".join(part or "" for part in parts).lower()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    for category, needles in CATEGORY_RULES:
        for needle in needles:
            if needle in SHORT_TOKEN_NEEDLES:
                if needle in tokens:
                    return category
            elif needle in text:
                return category
    return "General / Miscellaneous"


def feed_group(*parts: str) -> str:
    text = " ".join(part or "" for part in parts).lower()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    for group, needles in FEED_GROUP_RULES:
        for needle in needles:
            if needle in SHORT_TOKEN_NEEDLES:
                if needle in tokens:
                    return group
            elif needle in text:
                return group
    return "Reference / Occasional"


def session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess


def load_json_cache(name: str) -> dict[str, Any]:
    path = CACHE_DIR / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_json_cache(name: str, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / name).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def has_retryable_error(value: dict[str, Any]) -> bool:
    error = (value or {}).get("error", "")
    return bool(error) and any(token in error.lower() for token in ("timeout", "429", "502", "503", "504"))


def reddit_get(sess: requests.Session, url: str) -> dict[str, Any] | None:
    try:
        res = sess.get(url, timeout=5)
        maybe_pause_for_rate_limit(res)
        if res.status_code == 200:
            time.sleep(REQUEST_DELAY_SECONDS)
            return res.json()
        return {"_error": f"HTTP {res.status_code}"}
    except Exception as exc:
        return {"_error": str(exc)}


def maybe_pause_for_rate_limit(response: requests.Response) -> None:
    try:
        remaining = float(response.headers.get("X-Ratelimit-Remaining", "999"))
        reset = float(response.headers.get("X-Ratelimit-Reset", "0"))
    except ValueError:
        return
    if remaining <= RATE_LIMIT_FLOOR:
        time.sleep(max(RATE_LIMIT_PAUSE_SECONDS, min(reset, 30.0)))


def enrich_saved_posts(rows: list[dict[str, Any]], refresh: bool = False) -> dict[str, dict[str, Any]]:
    cache = {} if refresh else load_json_cache("saved_posts_public_info.json")
    ids = sorted({row["id"] for row in rows if row.get("id")})
    missing = [post_id for post_id in ids if post_id not in cache]
    sess = session()
    for start in range(0, len(missing), 100):
        batch = missing[start : start + 100]
        fullnames = ",".join(f"t3_{post_id}" for post_id in batch)
        data = reddit_get(sess, f"https://www.reddit.com/api/info.json?id={fullnames}")
        if not data or data.get("_error"):
            for post_id in batch:
                cache[post_id] = {"error": data.get("_error") if data else "unknown"}
            continue
        found = {}
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            found[post.get("id")] = post
        for post_id in batch:
            post = found.get(post_id)
            if post:
                cache[post_id] = {
                    "title": post.get("title", ""),
                    "subreddit": post.get("subreddit", ""),
                    "created_utc": post.get("created_utc"),
                    "score": post.get("score"),
                    "num_comments": post.get("num_comments"),
                    "domain": post.get("domain", ""),
                    "post_hint": post.get("post_hint", ""),
                    "permalink": post.get("permalink", ""),
                    "over_18": post.get("over_18", False),
                    "removed_by_category": post.get("removed_by_category"),
                }
            else:
                cache[post_id] = {"error": "not found"}
    save_json_cache("saved_posts_public_info.json", cache)
    return cache


def subreddit_about(subreddit: str, sess: requests.Session) -> dict[str, Any]:
    data = reddit_get(sess, f"https://www.reddit.com/r/{subreddit}/about.json")
    if not data or data.get("_error"):
        return {"display_name": subreddit, "error": data.get("_error") if data else "unknown"}
    about = data.get("data", {})
    return {
        "display_name": about.get("display_name") or subreddit,
        "title": about.get("title", ""),
        "public_description": about.get("public_description", ""),
        "subscribers": about.get("subscribers"),
        "active_user_count": about.get("active_user_count"),
        "over18": about.get("over18"),
        "created_utc": about.get("created_utc"),
        "subreddit_type": about.get("subreddit_type", ""),
        "quarantine": about.get("quarantine", False),
    }


def subreddit_post_cadence(subreddit: str, sess: requests.Session) -> dict[str, Any]:
    data = reddit_get(sess, f"https://www.reddit.com/r/{subreddit}/new.json?limit=100")
    if not data or data.get("_error"):
        return {"error": data.get("_error") if data else "unknown"}
    posts = [child.get("data", {}) for child in data.get("data", {}).get("children", [])]
    timestamps = [post.get("created_utc") for post in posts if post.get("created_utc")]
    if len(timestamps) < 2:
        return {"sample_posts": len(timestamps), "estimated_posts_per_day": len(timestamps)}
    span_days = max((max(timestamps) - min(timestamps)) / 86400, 1 / 24)
    return {
        "sample_posts": len(timestamps),
        "newest_post_utc": max(timestamps),
        "oldest_post_utc": min(timestamps),
        "sample_span_days": round(span_days, 2),
        "estimated_posts_per_day": round(len(timestamps) / span_days, 1),
    }


def fetch_about_worker(subreddit: str) -> tuple[str, dict[str, Any]]:
    sess = session()
    return subreddit.lower(), subreddit_about(subreddit, sess)


def fetch_cadence_worker(subreddit: str) -> tuple[str, dict[str, Any]]:
    sess = session()
    return subreddit.lower(), subreddit_post_cadence(subreddit, sess)


def enrich_subreddits(subreddits: list[str], refresh: bool = False, cadence_limit: int | None = None, workers: int = 8, retry_errors: bool = False) -> dict[str, dict[str, Any]]:
    cache = {} if refresh else load_json_cache("subreddit_metadata.json")
    missing_about = [
        subreddit
        for subreddit in subreddits
        if subreddit.lower() not in cache or (retry_errors and has_retryable_error(cache.get(subreddit.lower(), {})))
    ]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch_about_worker, subreddit) for subreddit in missing_about]
        for idx, future in enumerate(as_completed(futures), start=1):
            key, value = future.result()
            cache[key] = value
            if idx % 50 == 0:
                save_json_cache("subreddit_metadata.json", cache)

    cadence_targets = []
    for idx, subreddit in enumerate(subreddits, start=1):
        key = subreddit.lower()
        if "cadence" not in cache[key]:
            if cadence_limit is not None and idx <= cadence_limit:
                cadence_targets.append(subreddit)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as pool:
        futures = [pool.submit(fetch_cadence_worker, subreddit) for subreddit in cadence_targets]
        for idx, future in enumerate(as_completed(futures), start=1):
            key, value = future.result()
            cache.setdefault(key, {"display_name": key})
            cache[key]["cadence"] = value
            if idx % 25 == 0:
                save_json_cache("subreddit_metadata.json", cache)
    save_json_cache("subreddit_metadata.json", cache)
    return cache


def enrich_candidate_cadence(candidates: list[str], refresh: bool = False, workers: int = 8, retry_errors: bool = False) -> dict[str, dict[str, Any]]:
    cache = load_json_cache("subreddit_metadata.json")
    targets = []
    for subreddit in candidates:
        key = subreddit.lower()
        cache.setdefault(key, {"display_name": subreddit})
        if refresh or "cadence" not in cache[key] or (retry_errors and has_retryable_error(cache[key].get("cadence", {}))):
            targets.append(subreddit)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as pool:
        futures = [pool.submit(fetch_cadence_worker, subreddit) for subreddit in targets]
        for idx, future in enumerate(as_completed(futures), start=1):
            key, value = future.result()
            cache.setdefault(key, {"display_name": key})
            cache[key]["cadence"] = value
            if idx % 25 == 0:
                save_json_cache("subreddit_metadata.json", cache)
    save_json_cache("subreddit_metadata.json", cache)
    return cache


def month_year(value: Any) -> str:
    if not value:
        return ""
    try:
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc).strftime("%Y-%m")
    except Exception:
        return ""


def iso_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def score_saved_interest(count: int, subscribed: bool, recent_json_count: int) -> float:
    return count * 2.0 + (2.5 if subscribed else 0) + recent_json_count * 1.5


def clutter_score(sub: str, meta: dict[str, Any], saved_count: int) -> float:
    text = f"{sub} {meta.get('title','')} {meta.get('public_description','')}".lower()
    score = 0.0
    if saved_count == 0:
        score += 3.0
    if any(token in text for token in LOW_SIGNAL_DEFAULTS):
        score += 3.0
    if classify_text(sub, meta.get("title", ""), meta.get("public_description", "")) in {"Entertainment, Books & Media", "General / Miscellaneous"}:
        score += 1.0
    cadence = meta.get("cadence", {})
    ppd = cadence.get("estimated_posts_per_day")
    if isinstance(ppd, (int, float)) and ppd > 50:
        score += 2.0
    return score


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_outputs(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    subs = read_subscriptions()
    saved_csv = parse_saved_csv()
    saved_json = parse_saved_json()
    saved_rows = saved_csv + saved_json

    post_cache = enrich_saved_posts(saved_rows, refresh=args.refresh_posts) if args.fetch_posts else {}
    for row in saved_rows:
        post = post_cache.get(row["id"], {})
        for key in ("title", "subreddit", "created_utc", "score", "num_comments", "domain", "post_hint"):
            if not row.get(key) and post.get(key) is not None:
                row[key] = post.get(key)
        if post.get("permalink") and (not row.get("url") or row["url"].endswith(f"/comments/{row['id']}")):
            row["url"] = "https://www.reddit.com" + post["permalink"]
        row["post_created_month"] = month_year(row.get("created_utc"))
        row["post_created_date"] = iso_date(row.get("created_utc"))
        row["category"] = classify_text(row.get("subreddit", ""), row.get("title", ""), row.get("domain", ""))

    saved_by_sub = Counter(row.get("subreddit", "").lower() for row in saved_rows if row.get("subreddit"))
    saved_json_by_sub = Counter(row.get("subreddit", "").lower() for row in saved_json if row.get("subreddit"))

    sub_meta = enrich_subreddits(subs, refresh=args.refresh_subs, cadence_limit=args.cadence_limit, workers=args.workers, retry_errors=args.retry_errors) if args.fetch_subs else load_json_cache("subreddit_metadata.json")
    if args.cadence_for_candidates:
        subscribed = {s.lower(): s for s in subs}
        candidate_names: list[str] = []
        for sub, count in saved_by_sub.most_common(80):
            if count >= 3 and sub in subscribed:
                candidate_names.append(subscribed[sub])
        for sub in subs:
            if any(sig in sub.lower() for sig in DIGEST_SIGNALS):
                candidate_names.append(sub)
        deduped = list(dict.fromkeys(candidate_names))
        sub_meta = enrich_candidate_cadence(deduped, refresh=args.refresh_subs, workers=args.workers, retry_errors=args.retry_errors)
    subscription_rows: list[dict[str, Any]] = []
    for sub in subs:
        key = sub.lower()
        meta = sub_meta.get(key, {})
        cadence = meta.get("cadence", {})
        category = classify_text(sub, meta.get("title", ""), meta.get("public_description", ""))
        saved_count = saved_by_sub.get(key, 0)
        subscription_rows.append(
            {
                "subreddit": sub,
                "category": category,
                "feed_group": feed_group(sub, meta.get("title", ""), meta.get("public_description", "")),
                "subscribers": meta.get("subscribers"),
                "active_user_count": meta.get("active_user_count"),
                "over18": meta.get("over18"),
                "quarantine": meta.get("quarantine"),
                "subreddit_type": meta.get("subreddit_type"),
                "estimated_posts_per_day": cadence.get("estimated_posts_per_day"),
                "sample_posts": cadence.get("sample_posts"),
                "sample_span_days": cadence.get("sample_span_days"),
                "saved_posts_count": saved_count,
                "digest_candidate_score": round(score_saved_interest(saved_count, True, saved_json_by_sub.get(key, 0)), 1),
                "clutter_score": round(clutter_score(sub, meta, saved_count), 1),
                "title": meta.get("title", ""),
                "public_description": " ".join((meta.get("public_description") or "").split())[:400],
                "error": meta.get("error") or cadence.get("error") or "",
            }
        )

    subscription_rows.sort(key=lambda r: (r["category"], str(r["subreddit"]).lower()))
    write_csv(
        OUTPUT_DIR / "reddit_subscriptions_inventory_2026-05-02.csv",
        subscription_rows,
        [
            "subreddit",
            "category",
            "feed_group",
            "subscribers",
            "active_user_count",
            "over18",
            "quarantine",
            "subreddit_type",
            "estimated_posts_per_day",
            "sample_posts",
            "sample_span_days",
            "saved_posts_count",
            "digest_candidate_score",
            "clutter_score",
            "title",
            "public_description",
            "error",
        ],
    )

    write_csv(
        OUTPUT_DIR / "reddit_saved_posts_inventory_2026-05-02.csv",
        sorted(saved_rows, key=lambda r: (r.get("created_utc") or 0), reverse=True),
        [
            "source_file",
            "source_order",
            "id",
            "subreddit",
            "category",
            "title",
            "url",
            "post_created_date",
            "post_created_month",
            "score",
            "num_comments",
            "domain",
            "post_hint",
        ],
    )

    sub_summary_rows: list[dict[str, Any]] = []
    for sub, count in saved_by_sub.most_common():
        sample_titles = [row["title"] for row in saved_rows if row.get("subreddit", "").lower() == sub and row.get("title")][:3]
        sub_summary_rows.append(
            {
                "subreddit": sub,
                "saved_posts_count": count,
                "subscribed": sub in {s.lower() for s in subs},
                "category": classify_text(sub, " ".join(sample_titles)),
                "sample_titles": " | ".join(sample_titles),
            }
        )
    write_csv(
        OUTPUT_DIR / "reddit_saved_posts_by_subreddit_2026-05-02.csv",
        sub_summary_rows,
        ["subreddit", "saved_posts_count", "subscribed", "category", "sample_titles"],
    )

    category_counts = Counter(row["category"] for row in subscription_rows)
    saved_category_counts = Counter(row["category"] for row in saved_rows)
    digest_candidates = sorted(
        subscription_rows,
        key=lambda r: (float(r["digest_candidate_score"]), int(r["subscribers"] or 0) if str(r.get("subscribers") or "").isdigit() else 0),
        reverse=True,
    )
    digest_candidates = [
        row
        for row in digest_candidates
        if row["saved_posts_count"] >= 3 or any(sig in row["subreddit"].lower() for sig in DIGEST_SIGNALS)
    ][:35]
    clutter_candidates = sorted(subscription_rows, key=lambda r: (float(r["clutter_score"]), -int(r["saved_posts_count"] or 0)), reverse=True)[:60]

    write_csv(
        OUTPUT_DIR / "reddit_digest_candidates_2026-05-02.csv",
        digest_candidates,
        [
            "subreddit",
            "category",
            "feed_group",
            "saved_posts_count",
            "digest_candidate_score",
            "subscribers",
            "active_user_count",
            "estimated_posts_per_day",
            "sample_posts",
            "sample_span_days",
            "title",
            "public_description",
            "error",
        ],
    )

    write_csv(
        OUTPUT_DIR / "reddit_cleanup_candidates_2026-05-02.csv",
        clutter_candidates,
        [
            "subreddit",
            "category",
            "feed_group",
            "saved_posts_count",
            "clutter_score",
            "subscribers",
            "active_user_count",
            "estimated_posts_per_day",
            "over18",
            "quarantine",
            "subreddit_type",
            "title",
            "public_description",
            "error",
        ],
    )

    write_markdown_report(
        OUTPUT_DIR / "reddit_audit_2026-05-02.md",
        subs,
        saved_csv,
        saved_json,
        saved_rows,
        subscription_rows,
        sub_summary_rows,
        category_counts,
        saved_category_counts,
        digest_candidates,
        clutter_candidates,
        args,
    )


def table(rows: list[dict[str, Any]], columns: list[str], limit: int = 20) -> str:
    selected = rows[:limit]
    out = ["|" + "|".join(columns) + "|", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in selected:
        values = []
        for col in columns:
            value = row.get(col, "")
            if isinstance(value, float) and math.isfinite(value):
                value = f"{value:g}"
            value = str(value).replace("\n", " ").replace("|", "/")
            values.append(value[:120])
        out.append("|" + "|".join(values) + "|")
    return "\n".join(out)


def write_markdown_report(
    path: Path,
    subs: list[str],
    saved_csv: list[dict[str, Any]],
    saved_json: list[dict[str, Any]],
    saved_rows: list[dict[str, Any]],
    subscription_rows: list[dict[str, Any]],
    sub_summary_rows: list[dict[str, Any]],
    category_counts: Counter[str],
    saved_category_counts: Counter[str],
    digest_candidates: list[dict[str, Any]],
    clutter_candidates: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    created_values = [row.get("created_utc") for row in saved_rows if row.get("created_utc")]
    created_range = ""
    if created_values:
        created_range = f"{iso_date(min(created_values))} to {iso_date(max(created_values))}"

    lines = [
        "# Reddit Audit - 2026-05-02",
        "",
        "## File inventory",
        "",
        f"- `{SUBSCRIPTIONS_FILE.name}`: {len(subs):,} unique subscribed subreddits; one-column export named `current`.",
        f"- `{SAVED_CSV_FILE.name}`: {len(saved_csv):,} saved Reddit URLs/ids. The file itself has only `title` and `url`; `title` is actually the Reddit post id, not a human title.",
        f"- `{SAVED_JSON_FILE.name}`: {len(saved_json):,} rich Reddit listing items. It includes post metadata and an `after` cursor, so it appears to be one page of a larger listing rather than a full historical export.",
        "",
        "Important limitation: neither saved-post file contains a true `saved_at` timestamp. Public Reddit post metadata can recover when the post was created, not when you saved it. If the export/listing order was generated from Reddit's saved listing, row order may approximate save order, but month/year of save is not directly provable from these files.",
        "",
        f"- Enriched post-created date range: {created_range or 'not available without post metadata fetch'}.",
        f"- Public enrichment used: saved posts = `{args.fetch_posts}`, subreddits = `{args.fetch_subs}`.",
        "",
        "## Subscription inventory",
        "",
        table(
            [{"category": cat, "subreddits": count} for cat, count in category_counts.most_common()],
            ["category", "subreddits"],
            limit=20,
        ),
        "",
        "## Saved-post inventory",
        "",
        table(
            [{"category": cat, "saved_posts": count} for cat, count in saved_category_counts.most_common()],
            ["category", "saved_posts"],
            limit=20,
        ),
        "",
        "### Most-saved subreddits",
        "",
        table(sub_summary_rows, ["subreddit", "saved_posts_count", "subscribed", "category", "sample_titles"], limit=25),
        "",
        "## Best daily-digest candidates",
        "",
        "These are high-signal candidates because they either recur heavily in saved posts, match explicit AI/tech/work/music interests, or are current-event communities worth sampling through ranked top posts instead of the home feed.",
        "",
        table(
            digest_candidates,
            ["subreddit", "feed_group", "category", "saved_posts_count", "subscribers", "estimated_posts_per_day", "digest_candidate_score"],
            limit=35,
        ),
        "",
        "### Proposed future digest feed groups",
        "",
        table(
            [{"feed_group": group, "candidate_subreddits": ", ".join(row["subreddit"] for row in rows[:12])} for group, rows in group_digest_candidates(digest_candidates).items()],
            ["feed_group", "candidate_subreddits"],
            limit=10,
        ),
        "",
        "## Potential clutter candidates",
        "",
        "Treat this as a review queue, not an unsubscribe command. High scores mostly mean the subreddit has no saved-post signal, broad entertainment/default-feed dynamics, or high posting volume.",
        "",
        table(
            clutter_candidates,
            ["subreddit", "category", "saved_posts_count", "subscribers", "estimated_posts_per_day", "clutter_score", "title"],
            limit=60,
        ),
        "",
        "## Suggested operating system",
        "",
        "1. Split Reddit into multireddits/custom feeds by job-to-be-done: `AI Workbench`, `Career/Markets`, `Music Discovery`, `NYC/Travel/Food`, `Making/Design`, and `Reference Saves`.",
        "2. Unsubscribe from communities that are useful only occasionally; add them to a custom feed instead. This keeps the home feed from becoming the default inbox for every passing curiosity.",
        "3. For the daily digest, ingest only selected subreddits via top posts from the previous day, capped per category. Reddit should become a ranked briefing, not an infinite feed.",
        "4. For saved posts, create a weekly triage ritual in Raindrop: tag each new Reddit save as `read`, `use`, `archive`, or `delete`; only `use` items become tasks/projects.",
        "5. For historical saved posts, process in batches by subreddit/category rather than chronologically. Start with the top saved subreddits because that is where your old intent is densest.",
        "",
        "## Output files",
        "",
        "- `data/reddit/audit_outputs/reddit_subscriptions_inventory_2026-05-02.csv`",
        "- `data/reddit/audit_outputs/reddit_saved_posts_inventory_2026-05-02.csv`",
        "- `data/reddit/audit_outputs/reddit_saved_posts_by_subreddit_2026-05-02.csv`",
        "- `data/reddit/audit_outputs/reddit_digest_candidates_2026-05-02.csv`",
        "- `data/reddit/audit_outputs/reddit_cleanup_candidates_2026-05-02.csv`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def group_digest_candidates(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row.get("feed_group") or "Reference / Occasional", []).append(row)
    return groups


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Reddit subscriptions and saved posts.")
    parser.add_argument("--fetch-posts", action="store_true", help="Fetch public metadata for saved post ids.")
    parser.add_argument("--fetch-subs", action="store_true", help="Fetch public subreddit metadata.")
    parser.add_argument("--refresh-posts", action="store_true", help="Ignore saved post metadata cache.")
    parser.add_argument("--refresh-subs", action="store_true", help="Ignore subreddit metadata cache.")
    parser.add_argument("--cadence-limit", type=int, default=0, help="Only fetch posting-cadence samples for the first N subscribed subreddits. Default 0 skips broad cadence sampling.")
    parser.add_argument("--cadence-for-candidates", action="store_true", help="Fetch posting-cadence samples only for likely digest candidates.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent workers for public Reddit metadata fetches.")
    parser.add_argument("--retry-errors", action="store_true", help="Retry cached transient Reddit errors such as timeouts and rate limits.")
    args = parser.parse_args()
    build_outputs(args)


if __name__ == "__main__":
    main()
