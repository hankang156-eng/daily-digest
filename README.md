# Daily Digest

Personal daily reading digest for Hacker News, NYT/WSJ, selected blogs/research sites, and Reddit planning workflows.

Workspace:

```bash
daily_digest/
```

## Layout

| Path | Description |
|------|-------------|
| `src/daily_digest.py` | Main digest generator |
| `src/rankers/` | HN, NYT/WSJ, and blog/research rankers |
| `src/reddit/` | Reddit audit and cleanup/custom-feed workflows |
| `config/config.json` | Digest configuration |
| `scripts/` | Manual runner, LaunchAgent, and GitHub Pages setup |
| `data/hn/` | HN archive data and rendered archive views |
| `data/digest_archives/` | Selected digest archive data and logs |
| `data/reddit/` | Reddit audit inputs, audit outputs, cleanup mapping, and action plans |
| `output/daily_html/` | Date-stamped HTML digests |
| `output/daily_md/` | Date-stamped Markdown digests |
| `output/ranker_diagnostics/` | NYT/WSJ and blog ranker diagnostics |

## Manual Daily Run

Use the project virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements.txt
bash scripts/run_digest.sh
tail -80 data/digest_archives/digest.log
```

Run a digest for a prior input date. This fetches and ranks content from the previous day:

```bash
bash scripts/run_digest.sh --date 2026-05-24
```

### API keys (`.env`)

API keys are read from a gitignored `.env` at the repo root, which `scripts/run_digest.sh` sources for both manual and scheduled (LaunchAgent) runs. Copy the template and fill in what you need:

```bash
cp .env.example .env
# edit .env
```

| Key | Used for |
|-----|----------|
| `GEMINI_API_KEY` | LLM article overviews when `--model gemini` |
| `NYT_API_KEY` | NYT article word counts via the Article Search API (free key from developer.nytimes.com) |
| `ANTHROPIC_API_KEY` | Overviews when `--model claude-sonnet` / `claude-opus` |

### Article overviews

NYT items always show their **Abstract** (the RSS description, free, no LLM). The richer **Overview** is LLM-generated and **opt-in**: by default no LLM summaries are generated. Opt in with `--model gemini`, which uses `gemini-3.5-flash` (falls back to `gemini-3.1-flash-lite` on rate limits, with retry/backoff and throttling):

```bash
bash scripts/run_digest.sh --model gemini
```

Summary behavior by provider:

| Provider | NYT | Blogs / research |
|----------|-----|------------------|
| `none` (default) | Abstract only | none |
| `gemini` | Abstract + Gemini Overview | Gemini Overview |
| `claude-sonnet` / `claude-opus` | Abstract + Claude Overview for `--sections` (others get Gemini Overview) | Claude Overview |

```bash
bash scripts/run_digest.sh --model claude-sonnet --sections "Technology / AI,Opinion / Analysis"
bash scripts/run_digest.sh --model claude-opus --sections all
```

Override the provider's default model when needed:

```bash
bash scripts/run_digest.sh --model gemini --model-id gemini-3.1-flash-lite
```

Overviews are cached per `provider|model|article` in `output/ranker_diagnostics/article_overview_cache.json`, so re-running a date with the same provider makes no API calls. Bump `OVERVIEW_CACHE_VERSION` in `src/daily_digest.py` when changing the prompt or generation params so stale entries are not reused.

### NYT word counts

When `NYT_API_KEY` is set, NYT articles get accurate word counts and reading times from the NYT Article Search API instead of scraping (NYT returns 403 to scrapers). Without the key, NYT reading stats are simply skipped. (WSJ is no longer fetched.)

### Dates

The title date is the **digest date** — the day the digest represents (content date + 1): HN shows the previous day's top stories, NYT shows that day's news. The header also shows `Fetched: <weekday, date, time>` = when the digest was compiled (always now, even for backfill). NYT items show `Published: <weekday, YYYY-MM-DD>`. Output filenames use the digest date (`digest_YYYY-MM-DD.*`).

`scripts/run_digest.sh` automatically uses `.venv/bin/python3` when it exists, installs dependencies as a fast no-op, sources `.env`, runs `src/daily_digest.py`, writes `data/digest_archives/digest.log`, and lets `src/daily_digest.py` push to GitHub Pages when enabled in `config/config.json`.

## Main Outputs

| File | Description |
|------|-------------|
| `output/daily_html/digest_YYYY-MM-DD.html` | Styled web/mobile digest |
| `output/daily_md/digest_YYYY-MM-DD.md` | Obsidian-friendly markdown digest |
| `index.html` | Exact copy of the latest digest for GitHub Pages |
| `digest_archive.html` | Links to saved daily HTML digests from newest to oldest |
| `data/hn/hn_archive.md` / `data/hn/hn_archive.xlsx` | HN top-10 archive views |
| `data/hn/hn_archive_data.json` | Raw HN archive data |
| `data/digest_archives/dd_archive.md` / `data/digest_archives/dd_archive.xlsx` | Selected digest item archive views |
| `data/digest_archives/dd_archive_data.json` | Raw digest archive data |

## Ranker Diagnostics

The main digest is the canonical output. The rankers also write diagnostic files in `output/ranker_diagnostics/`:

```bash
output/ranker_diagnostics/nyt_wsj_briefing_YYYY-MM-DD.md
output/ranker_diagnostics/nyt_wsj_candidates_YYYY-MM-DD.csv
output/ranker_diagnostics/blog_briefing_YYYY-MM-DD.md
output/ranker_diagnostics/blog_candidates_YYYY-MM-DD.csv
```

Run rankers directly when tuning:

```bash
.venv/bin/python3 src/rankers/nyt_wsj_rss_ranker.py --date 2026-04-29 --max-links 20
.venv/bin/python3 src/rankers/blog_reading_ranker.py --date 2026-04-29 --max-links 20
```

Use `read_urls.txt` in the project root or `output/ranker_diagnostics/` to suppress URLs you have already read or do not want to see again.

## Reddit Workflows

Audit existing Reddit exports:

```bash
.venv/bin/python3 src/reddit/reddit_audit.py --fetch-posts --fetch-subs --cadence-for-candidates
```

Create a dry-run Reddit cleanup/custom-feed plan from the approved mapping:

```bash
.venv/bin/python3 src/reddit/reddit_cleanup.py --username YOUR_REDDIT_USERNAME
```

The cleanup workflow defaults to dry-run. Account-changing actions require Reddit OAuth environment variables and explicit execution flags:

```bash
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
export REDDIT_REFRESH_TOKEN=...

.venv/bin/python3 src/reddit/reddit_cleanup.py --execute --execute-custom-feeds
.venv/bin/python3 src/reddit/reddit_cleanup.py --execute --execute-prune
```

Execution discovers the authenticated username via Reddit `/api/v1/me`; the optional `--username` is only for dry-run RSS link generation.

The Reddit scripts use this User-Agent, matching Reddit's requested format:

```text
script:daily-digest-cleanup:v0.1.0 (by /u/miss_comte)
```

Reddit metadata caches in `data/reddit/audit_outputs/` are for short-term analysis. To keep local storage aligned with Reddit's deleted-content expectations, periodically delete or refresh:

```bash
rm data/reddit/audit_outputs/saved_posts_public_info.json
rm data/reddit/audit_outputs/subreddit_metadata.json
```

## Configuration

Important defaults in `config/config.json`:

```json
{
  "settings": {
    "hn_digest_count": 16,
    "nyt_wsj_max_links": 20,
    "blog_max_links": 20,
    "ranker_output_dir": "output/ranker_diagnostics"
  },
  "github_pages": {
    "enabled": true
  }
}
```

## HN Historical Seed

Seed up to the past year:

```bash
.venv/bin/python3 src/rankers/hn_historical.py --days 365 --top 10 --refresh
```

Quick 7-day test:

```bash
.venv/bin/python3 src/rankers/hn_archive_sample.py
```

After this, `src/daily_digest.py` handles future daily HN archive entries.

## Failure Behavior

Every fetch is isolated. One failed source should not crash the run.

If no real network-fetched content is available, `src/daily_digest.py` preserves the existing digest/index/archive files and skips GitHub publishing. That prevents DNS or network failures from replacing a good digest with a placeholder.
