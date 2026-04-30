#!/usr/bin/env python3
"""
Seed the HackerNews archive with historical daily top stories.

Usage:
  python3 hn_historical.py
  python3 hn_historical.py --days 7 --top 10
"""

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from daily_digest import write_hn_archive_xlsx
    HAS_XLSX_EXPORT = True
except Exception:
    HAS_XLSX_EXPORT = False


SCRIPT_DIR = Path(__file__).parent.resolve()
HTTP_HEADERS = {"User-Agent": "Daily Digest historical archive builder"}


def unix_range(date):
    start = datetime.datetime.combine(date, datetime.time.min)
    end = datetime.datetime.combine(date, datetime.time.max)
    return int(start.timestamp()), int(end.timestamp())


def wanted_dates(days):
    today = datetime.date.today()
    return [today - datetime.timedelta(days=offset) for offset in range(days, 0, -1)]


def fetch_day(date, n=10, retries=4, tag="story", hits_per_page=1000):
    if not HAS_REQUESTS:
        print("    Error: requests is not installed.")
        return []

    start, end = unix_range(date)
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?tags={tag}&hitsPerPage={hits_per_page}"
        f"&numericFilters=created_at_i>{start},created_at_i<{end}"
    )

    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=20, headers=HTTP_HEADERS)
            if response.status_code == 429:
                wait = min(120, 2 ** attempt * 10)
                print(f"    Rate limited on {date}; waiting {wait}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            stories = []
            for hit in response.json().get("hits", []):
                object_id = hit.get("objectID")
                title = (hit.get("title") or "").strip()
                if not title:
                    continue
                stories.append({
                    "date": date.isoformat(),
                    "title": title,
                    "url": hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
                    "hn_url": f"https://news.ycombinator.com/item?id={object_id}",
                    "score": hit.get("points", 0) or 0,
                    "comments": hit.get("num_comments", 0) or 0,
                    "author": hit.get("author", ""),
                })
            stories = dedupe_stories(stories)
            stories.sort(key=lambda item: item["score"], reverse=True)
            return stories[:n]
        except Exception as e:
            if attempt == retries - 1:
                print(f"    Error on {date}: {e}")
                return []
            time.sleep(min(60, 2 ** attempt * 3))
    return []


def dedupe_stories(stories):
    seen = set()
    unique = []
    for story in stories:
        key = (story.get("url") or story.get("hn_url") or story.get("title", "")).rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(story)
    return unique


def write_md_table(data, path):
    lines = [
        "# HackerNews Daily Top 10 - Archive",
        "",
        f"*Last updated: {datetime.date.today().isoformat()} · "
        f"{len(data)} days · {sum(len(v) for v in data.values())} stories*",
        "",
        "| Date | Day | Rank | Title | Points | Comments | Topic | URL |",
        "|------|-----|------|-------|--------|----------|-------|-----|",
    ]
    for date_str in sorted(data.keys(), reverse=True):
        day = datetime.date.fromisoformat(date_str).strftime("%A")
        for rank, story in enumerate(data[date_str], 1):
            title = story.get("title", "").replace("\n", " ").replace("|", "\\|")
            url = story.get("url", "")
            hn_url = story.get("hn_url", "")
            link = f"[link]({url})" if url else ""
            hn_link = f"[HN]({hn_url})" if hn_url else ""
            links = " · ".join(part for part in (link, hn_link) if part)
            lines.append(
                f"| {date_str} | {day} | {rank} | {title} | "
                f"{int(story.get('score', 0))} | {int(story.get('comments', 0))} | "
                f"Technology | {links} |"
            )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  [MD] Saved: {path}")


def main():
    parser = argparse.ArgumentParser(description="Build or fill the HN historical archive")
    parser.add_argument("--days", type=int, default=365, help="Days to cover, counting backward from yesterday")
    parser.add_argument("--top", type=int, default=10, help="Stories per day")
    parser.add_argument(
        "--tag",
        choices=("story", "front_page"),
        default="story",
        help="Algolia tag to query. Use story for complete historical top-N results; front_page is sparse historically.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch requested dates even if they already exist in hn_archive_data.json.",
    )
    args = parser.parse_args()

    json_path = SCRIPT_DIR / "hn_archive_data.json"
    existing = {}
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as f:
                existing = json.load(f)
            print(f"  Found existing archive with {len(existing)} days.")
        except Exception as e:
            print(f"  Error reading existing archive: {e}; starting fresh.")

    dates = wanted_dates(args.days)
    missing = [date for date in dates if args.refresh or date.isoformat() not in existing]
    mode = "refreshing" if args.refresh else "fetching missing"
    print(f"\n{mode.capitalize()} {len(missing)} days out of the last {args.days} days.")
    print(f"Using Algolia tag={args.tag!r}; results are sorted by points descending.")
    print("(~1 request/sec, with exponential backoff on 429 responses)\n")

    fetched = {}
    for index, date in enumerate(missing, 1):
        sys.stdout.write(f"\r  [{index:3d}/{len(missing)}] {date}  ")
        sys.stdout.flush()
        stories = fetch_day(date, n=args.top, tag=args.tag)
        if stories:
            fetched[date.isoformat()] = stories
        time.sleep(1.0)

    merged = {**existing, **fetched}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"\n  [JSON] Saved: {json_path} ({len(merged)} days total)")

    write_md_table(merged, SCRIPT_DIR / "hn_archive.md")
    if HAS_XLSX_EXPORT:
        write_hn_archive_xlsx(merged, SCRIPT_DIR / "hn_archive.xlsx")
        print(f"  [XLSX] Saved: {SCRIPT_DIR / 'hn_archive.xlsx'}")
    else:
        print("  [XLSX] Skipped: xlsx export helper could not be imported.")
    print("\n  Archive complete.\n")


if __name__ == "__main__":
    main()
