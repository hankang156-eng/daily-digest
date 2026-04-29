#!/usr/bin/env python3
"""
HackerNews Historical Archive Builder
Fetches the top 10 HN stories for every day over the past N days.
Outputs:
  - hn_archive_data.json  — raw data (used by daily_digest.py for daily updates)
  - hn_archive.md         — searchable markdown table

Run this ONCE to seed the archive with historical data.
After that, daily_digest.py appends each new day automatically.

Usage:
  python3 hn_historical.py           # full 365-day build
  python3 hn_historical.py --days 7  # quick test with last 7 days
"""

import sys
import json
import time
import datetime
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("FATAL: pip3 install requests --break-system-packages")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent.resolve()

# ─── Topic inference (mirrors daily_digest.py) ────────────────────────────────

def _infer_topic(s):
    return "Technology"   # All HN stories are tech

# ─── Fetching ─────────────────────────────────────────────────────────────────

def unix_range(date):
    start = datetime.datetime.combine(date, datetime.time.min)
    end   = datetime.datetime.combine(date, datetime.time.max)
    return int(start.timestamp()), int(end.timestamp())

def fetch_day(date, n=10, retries=3):
    """Fetch top-n HN front-page stories for a given date via Algolia."""
    start, end = unix_range(date)
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?tags=front_page&hitsPerPage=50"
        f"&numericFilters=created_at_i>{start},created_at_i<{end}"
    )
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"    Rate limited — waiting {wait}s…")
                time.sleep(wait)
                continue
            r.raise_for_status()
            hits = r.json().get("hits", [])
            stories = []
            for h in hits:
                stories.append({
                    "date":     date.isoformat(),
                    "title":    h.get("title", "").strip(),
                    "url":      h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    "hn_url":   f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    "score":    h.get("points", 0) or 0,
                    "comments": h.get("num_comments", 0) or 0,
                    "author":   h.get("author", ""),
                })
            stories.sort(key=lambda x: x["score"], reverse=True)
            return stories[:n]
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                print(f"    Error on {date}: {e}")
                return []
    return []

def fetch_all_days(days=365, n_per_day=10):
    today   = datetime.date.today()
    results = {}
    total   = 0

    print(f"\nFetching {days} days of HN top-{n_per_day} stories…")
    print("(~1 request/sec — takes about", round(days / 60, 1), "minutes)\n")

    for i in range(days):
        date = today - datetime.timedelta(days=days - i)
        sys.stdout.write(f"\r  [{i+1:3d}/{days}] {date}  ")
        sys.stdout.flush()
        stories = fetch_day(date, n=n_per_day)
        if stories:
            results[date.isoformat()] = stories
            total += len(stories)
        time.sleep(1.0)

    print(f"\n\n  ✓ Fetched {total} stories across {len(results)} days.\n")
    return results

# ─── Markdown Table Output ────────────────────────────────────────────────────

def write_md_table(data, path):
    """Write hn_archive.md as a sorted markdown table (newest first)."""
    lines = [
        "# HackerNews Daily Top 10 — Archive",
        "",
        f"*Last updated: {datetime.date.today().isoformat()} · "
        f"{len(data)} days · "
        f"{sum(len(v) for v in data.values())} stories*",
        "",
        "| Date | Day | Rank | Title | Points | Comments | Topic | URL |",
        "|------|-----|------|-------|--------|----------|-------|-----|",
    ]
    for date_str in sorted(data.keys(), reverse=True):
        day = datetime.date.fromisoformat(date_str).strftime("%A")
        for rank, s in enumerate(data[date_str], 1):
            title   = s["title"].replace("|", "\\|")
            url     = s.get("url", "")
            hn_url  = s.get("hn_url", "")
            link    = f"[link]({url})" if url else ""
            hn_link = f"[HN]({hn_url})" if hn_url else ""
            links   = " · ".join(filter(None, [link, hn_link]))
            lines.append(
                f"| {date_str} | {day} | {rank} | {title} "
                f"| {s.get('score',0)} | {s.get('comments',0)} "
                f"| Technology | {links} |"
            )
    content = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [MD] Saved: {path}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build HN historical archive")
    parser.add_argument("--days", type=int, default=365, help="Days to fetch (default 365)")
    parser.add_argument("--top",  type=int, default=10,  help="Stories per day (default 10)")
    args = parser.parse_args()

    json_path = SCRIPT_DIR / "hn_archive_data.json"

    # If JSON already exists, merge rather than overwrite
    existing = {}
    if json_path.exists():
        try:
            with open(json_path) as f:
                existing = json.load(f)
            print(f"  Found existing archive with {len(existing)} days — will fill gaps only.\n")
        except Exception:
            existing = {}

    data = fetch_all_days(days=args.days, n_per_day=args.top)

    # Merge: existing entries win (don't re-fetch days already collected)
    merged = {**data, **existing}

    with open(json_path, "w") as f:
        json.dump(merged, f, ensure_ascii=False)
    print(f"  [JSON] Saved: {json_path} ({len(merged)} days total)")

    md_path = SCRIPT_DIR / "hn_archive.md"
    write_md_table(merged, md_path)

    print("\n  ✓ Archive complete!")
    print(f"     JSON: {json_path}")
    print(f"     MD:   {md_path}\n")

if __name__ == "__main__":
    main()
