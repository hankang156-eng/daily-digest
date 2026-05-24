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

Article overviews default to **none** — no LLM summaries are generated unless the user passes `--summary-provider`. Opt in with `--summary-provider gemini` (model `gemini-3.5-flash`, automatic fallback to `gemini-3.1-flash-lite` on 429/503, with retry/backoff and a per-call throttle). The Claude providers (`claude-sonnet`, `claude-opus`) require `--summary-nyt-sections`.

NYT word counts come from the NYT Article Search API, not scraping (NYT returns 403 to the scraper). Filtering by `fq=web_url` returns zero results in this API version; instead the code derives a `q` query from the URL slug, searches, and exact-matches the returned `web_url` (see `_nyt_search_word_count`). Lookups are per-URL, throttled, cached, and degrade to scraping (then to nothing) when the key is missing or an article isn't found.

## Path Conventions

Do not hardcode the checkout path. Python modules should derive paths from `Path(__file__).resolve()` and shell scripts should derive paths from their script directory.

`scripts/com.michelle.dailydigest.plist` is a template. `scripts/setup_launchagent.sh` fills in the current repo path when installing it to `~/Library/LaunchAgents/`.

## Safety Notes

The main digest should preserve existing digest/index/archive files when network fetches fail or no real content is available.

Reddit cleanup defaults to dry-run. Account-changing behavior requires Reddit OAuth environment variables and explicit `--execute` flags.

Generated outputs and large caches are part of the local workflow. Avoid deleting data under `data/` or `output/` unless the user explicitly asks.

Before changing scheduler behavior, verify both `scripts/run_digest.sh` and `scripts/setup_launchagent.sh`.
