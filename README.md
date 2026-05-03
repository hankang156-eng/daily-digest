# Daily Digest

Personal daily reading digest for Hacker News, NYT/WSJ, and selected blogs/research sites.

Workspace:

```bash
/Users/michellekang/Documents/daily_digest
```

## Manual Daily Run

Use the project virtual environment:

```bash
cd /Users/michellekang/Documents/daily_digest
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements.txt
bash run_digest.sh
tail -80 digest.log
```

`run_digest.sh` automatically uses `.venv/bin/python3` when it exists, installs dependencies as a fast no-op, runs `daily_digest.py`, writes `digest.log`, and lets `daily_digest.py` push to GitHub Pages when enabled in `config.json`.

## Main Outputs

| File | Description |
|------|-------------|
| `daily_html/digest_YYYY-MM-DD.html` | Styled web/mobile digest |
| `daily_md/digest_YYYY-MM-DD.md` | Obsidian-friendly markdown digest |
| `index.html` | Exact copy of the latest digest for GitHub Pages |
| `hn_archive.md` / `hn_archive.xlsx` | HN top-10 archive views |
| `hn_archive_data.json` | Raw HN archive data |
| `dd_archive.md` / `dd_archive.xlsx` | Selected digest item archive views |
| `dd_archive_data.json` | Raw digest archive data |

## Digest Structure

The digest is now one reading list, not Espresso/Lungo.

- **Hacker News Top 16**: previous day's top HN stories by points, with numbering, score/comment metadata, and discussion links.
- **NYT / WSJ Strategic Reading List**: up to 20 ranked articles, grouped by the most relevant NYT/WSJ section tag.
- **MIT & Sloan Research**: MIT IDE, MIT Shaping Work, and MIT Sloan Review items split out from the blog list.
- **Blogs & Craft**: ranked blog posts from security, engineering, strategy, and craft sources.
- **LinkedIn - Rama's Activity**: direct placeholder link.

## Ranker Diagnostics

The main digest is the canonical output. The rankers also write diagnostic files in `output/`:

```bash
output/nyt_wsj_briefing_YYYY-MM-DD.md
output/nyt_wsj_candidates_YYYY-MM-DD.csv
output/blog_briefing_YYYY-MM-DD.md
output/blog_candidates_YYYY-MM-DD.csv
```

Run rankers directly when tuning:

```bash
.venv/bin/python3 nyt_wsj_rss_ranker.py --date 2026-04-29 --max-links 20 --output-dir output
.venv/bin/python3 blog_reading_ranker.py --date 2026-04-29 --max-links 20 --output-dir output
```

## NYT / WSJ Ranker

`nyt_wsj_rss_ranker.py`:

- Fetches configured NYT/WSJ RSS feeds.
- Attempts RSS/Atom discovery from section pages when exact feed URLs are unknown.
- Deduplicates by canonical URL and fuzzy title matching.
- Scores articles by section weight, keyword relevance, recency, cross-section signal, source differentiation, and opinion selectivity.
- Applies diversity caps so the list does not become all politics, markets, or AI.

NYT is weighted toward narrative, policy, social, health, and institutional context. WSJ is additive when it adds markets, capital, corporate, management, M&A, enterprise technology, or investor signal.

## Blog / Research Ranker

`blog_reading_ranker.py`:

- Fetches RSS/Atom feeds where available.
- Uses respectful metadata scraping for MIT pages, Paul Graham, and Neal.fun when feeds are unavailable.
- Filters mainly to the target date.
- Uses `output/blog_cache.json` to detect newly seen low-frequency/no-date items.
- Scores by source weight, category, user-interest keywords, freshness, durability, and category/source diversity.

Use `read_urls.txt` in the project root or `output/` to suppress URLs you have already read or do not want to see again.

## Configuration

Important defaults in `config.json`:

```json
{
  "settings": {
    "hn_digest_count": 16,
    "nyt_wsj_max_links": 20,
    "blog_max_links": 20,
    "ranker_output_dir": "output"
  },
  "github_pages": {
    "enabled": true
  }
}
```

Optional advanced overrides:

- Add `nyt_wsj_ranker.feeds` to override or add NYT/WSJ feed definitions.
- Add `blog_ranker.sources` to override blog/research source definitions.
- Leave uncertain feed URLs blank and provide `section_url`; the ranker will try discovery and log failures without crashing.

## GitHub Pages

Run once if the repo is not configured yet:

```bash
bash setup_github_pages.sh
```

Then enable automatic push:

```json
"github_pages": { "enabled": true }
```

The script pushes the latest digest, archives, Excel archive files, and date-specific ranker diagnostic files.

## HN Historical Seed

Seed up to the past year:

```bash
.venv/bin/python3 hn_historical.py --days 365 --top 10 --refresh
```

Quick 7-day test:

```bash
.venv/bin/python3 hn_archive_sample.py
```

After this, `daily_digest.py` handles future daily HN archive entries.

## Failure Behavior

Every fetch is isolated. One failed source should not crash the run.

If no real network-fetched content is available, `daily_digest.py` preserves the existing digest/index/archive files and skips GitHub publishing. That prevents DNS or network failures from replacing a good digest with a placeholder.
