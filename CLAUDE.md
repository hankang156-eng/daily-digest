# Daily Digest Agent Notes

## Project Overview

Daily Digest is a personal Python project that builds a daily reading digest from Hacker News, NYT/WSJ RSS, selected blogs/research sites, and Reddit planning workflows.

The repository is intentionally file-based:

- `src/daily_digest.py` is the main digest generator.
- `src/rankers/` contains Hacker News archive helpers, NYT/WSJ RSS ranking, and blog/research ranking.
- `src/reddit/` contains Reddit audit and cleanup/custom-feed planning tools.
- `config/config.json` controls digest counts, ranker output paths, and GitHub Pages publishing.
- `scripts/` contains the manual runner, LaunchAgent installer/template, and GitHub Pages setup helper.
- `data/hn/` and `data/digest_archives/` hold archive data and rendered archive views.
- `data/reddit/` holds Reddit audit inputs, analysis outputs, cleanup mappings, and action plans.
- `output/daily_html/`, `output/daily_md/`, and `output/ranker_diagnostics/` hold generated digest and ranker outputs.
- `digest_archive.html` is generated from `output/daily_html/digest_*.html` and links to saved digests newest-first.
- `tests/` currently covers Reddit cleanup planning behavior.

## Environment

Use the local virtual environment from the repo root:

```bash
.venv/bin/python3 -m pip install -r requirements.txt
```

The current venv uses Python 3.14. Prefer invoking tools through `.venv/bin/python3` so imports resolve consistently.

## Common Commands

Run tests:

```bash
.venv/bin/python3 -m unittest discover -s tests
```

Run the full digest workflow:

```bash
bash scripts/run_digest.sh
tail -80 data/digest_archives/digest.log
```

Run for a user input date, using articles from the prior day:

```bash
bash scripts/run_digest.sh --date 2026-05-24
```

Run rankers directly while tuning:

```bash
.venv/bin/python3 src/rankers/nyt_wsj_rss_ranker.py --date 2026-04-29 --max-links 20
.venv/bin/python3 src/rankers/blog_reading_ranker.py --date 2026-04-29 --max-links 20
```

Run Reddit audit and cleanup planning:

```bash
.venv/bin/python3 src/reddit/reddit_audit.py --fetch-posts --fetch-subs --cadence-for-candidates
.venv/bin/python3 src/reddit/reddit_cleanup.py --username YOUR_REDDIT_USERNAME
```

Install or refresh the macOS LaunchAgent after moving the checkout:

```bash
bash scripts/setup_launchagent.sh
```

## API Keys & Article Summaries

API keys live in a gitignored `.env` at the repo root (template: `.env.example`). `scripts/run_digest.sh` sources `.env` near the top, so both manual and scheduled (LaunchAgent) runs pick up keys without an `EnvironmentVariables` block in the plist. Relevant keys: `GEMINI_API_KEY`, `NYT_API_KEY`, `ANTHROPIC_API_KEY`.

NYT items always show their RSS **abstract** (`fill_nyt_abstracts`, free, no LLM), rendered as "Abstract:". The richer LLM **"Overview:"** is opt-in via `--model`. Default is **none** (abstract only). With `gemini`: NYT and blogs get a Gemini overview (`gemini-3.5-flash`, fallback to `gemini-3.1-flash-lite` on 429/503, retry/backoff + per-call throttle). With `claude-sonnet`/`claude-opus` (requires `--sections`): blogs and the listed NYT sections get Claude; other NYT sections fall back to Gemini. WSJ is no longer fetched (NYT-only ranker).

Overviews are cached per `OVERVIEW_CACHE_VERSION|provider|model|url|title` in `output/ranker_diagnostics/article_overview_cache.json` — a full-result cache (a hit makes no API call). Re-running a date with the same provider costs nothing; switching providers only bills the newly requested entries. Bump `OVERVIEW_CACHE_VERSION` when changing the prompt or generation params so stale entries are not reused. The Gemini call sets `thinkingConfig.thinkingBudget=0` and `maxOutputTokens=800` (Gemini 3.x thinking tokens otherwise consume the budget and truncate output). Calls are spaced `GEMINI_MIN_INTERVAL` (5s, sized to free-tier RPM); the primary model fails fast (1 attempt) and a per-run circuit breaker skips it after `GEMINI_PRIMARY_429_LIMIT` 429s — free-tier `gemini-3.5-flash` often exhausts its quota mid-run, so `gemini-3.1-flash-lite` is the practical workhorse. This keeps a full summary run ~2 min instead of ~12.

**NYT source routing** (`run_ranker`, `nyt_wsj_rss_ranker.py`): recent dates (news date within `ARCHIVE_RECENCY_DAYS`=3) use **live RSS**; older/backfill dates use the **NYT Archive API** as the article *source* — fetch the month, filter to the news date, score with the usual signals plus a print-front-page prominence bonus and a `type_of_material` filter. The news date is the digest date (content + 1). Archive-sourced items carry `word_count` + `abstract` inline (no Article Search calls). Archive months cache forever under `output/ranker_diagnostics/nyt_archive/` (gitignored, ~11MB/month). Requires `NYT_API_KEY`; without it, backfill falls back to live RSS (current articles, wrong date).

For RSS-sourced (recent) NYT items, word counts come from the NYT Article Search API, not scraping (NYT returns 403 to the scraper). Filtering by `fq=web_url` returns zero results in this API version; instead the code derives a `q` query from the URL slug, searches, and exact-matches the returned `web_url` (see `_nyt_search_word_count`). Lookups are per-URL, throttled, cached, and degrade to scraping (then to nothing) when the key is missing or an article isn't found.

The **digest date** (title, output filenames, archive labels) is the content date + 1 — the day the digest represents (HN = previous day's top, NYT = that day's news); see `digest_date_for`. The header also shows `Fetched:` = compile timestamp (always now, even for backfill). HN archive and `dd_archive` still key off the content date.

## Path Conventions

Do not hardcode the checkout path. Python modules should derive paths from `Path(__file__).resolve()` and shell scripts should derive paths from their script directory.

`scripts/com.michelle.dailydigest.plist` is a template. `scripts/setup_launchagent.sh` fills in the current repo path when installing it to `~/Library/LaunchAgents/`.

## Safety Notes

The main digest should preserve existing digest/index/archive files when network fetches fail or no real content is available.

Reddit cleanup defaults to dry-run. Account-changing behavior requires Reddit OAuth environment variables and explicit `--execute` flags.

Generated outputs and large caches are part of the local workflow. Avoid deleting data under `data/` or `output/` unless the user explicitly asks.

Before changing scheduler behavior, verify both `scripts/run_digest.sh` and `scripts/setup_launchagent.sh`.
