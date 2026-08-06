# Daily Digest Agent Notes

## Project Overview

Daily Digest is a personal Python project that builds a daily reading digest from Hacker News, NYT/WSJ RSS, selected blogs/research sites, and Reddit planning workflows.

The repository is intentionally file-based:

- `src/daily_digest.py` is the main digest generator.
- `src/rankers/` contains Hacker News archive helpers, NYT/WSJ RSS ranking, and blog/research ranking.
- `src/comprehension/` turns the digest into tracked storylines — see "Comprehension Layer" below.
- `src/reddit/` contains Reddit audit and cleanup/custom-feed planning tools.
- `config/config.json` controls digest counts, ranker output paths, comprehension settings, and GitHub Pages publishing.
- `config/reader_profile.md` is the hand-owned reader context the comprehension layer explains *to*. **Gitignored on purpose** — it carries Merge4 framing and this repo publishes to GitHub Pages. Back it up outside git; `config/reader_profile.example.md` is the committed template.
- `scripts/` contains the manual runner, LaunchAgent installer/template, and GitHub Pages setup helper.
- `data/hn/` and `data/digest_archives/` hold archive data and rendered archive views.
- `data/reddit/` holds Reddit audit inputs, analysis outputs, cleanup mappings, and action plans.
- `output/daily_html/`, `output/daily_md/`, and `output/ranker_diagnostics/` hold generated digest and ranker outputs.
- `output/digest_json/digest_<digest-date>.json` is the lossless machine-readable record of each run — the contract the comprehension layer consumes (gitignored).
- `output/comprehension/` holds the companion pages and weekly essays; `data/comprehension/` holds the thread registry and reading marks.
- `digest_archive.html` is generated from `output/daily_html/digest_*.html` and links to saved digests newest-first.
- `tests/` covers the JSON contract, the comprehension layer, the ai-infra track, digest rendering, and Reddit cleanup planning.

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

Run the comprehension layer (also runs automatically as step `[3/4]` of `run_digest.sh`):

```bash
.venv/bin/python3 -m src.comprehension.run --date 2026-08-04
.venv/bin/python3 -m src.comprehension.run --report
.venv/bin/python3 -m src.comprehension.run --weekly --date 2026-08-04
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

Note that `.env` lives at the *repo root* and is gitignored, so a git worktree does not have one — run tools from a worktree with the keys sourced explicitly (`set -a; . ../../../.env; set +a`) or they will silently produce no summaries. `ANTHROPIC_API_KEY` powers both the optional Claude overviews and the whole comprehension layer.

NYT items always show their RSS **abstract** (`fill_nyt_abstracts`, free, no LLM), rendered as "Abstract:". The richer LLM **"Overview:"** is controlled by `--model`; default is **gemini** so the daily cron generates overviews automatically (pass `--model none` for abstract-only). With `gemini`: NYT and blogs get a Gemini overview (`gemini-3.5-flash`, fallback to `gemini-3.1-flash-lite` on 429/503, retry/backoff + per-call throttle). With `claude-sonnet`/`claude-opus` (requires `--sections`): blogs and the listed NYT sections get Claude; other NYT sections fall back to Gemini. WSJ is no longer fetched (NYT-only ranker).

Overviews are cached per `OVERVIEW_CACHE_VERSION|provider|model|url|title` in `output/ranker_diagnostics/article_overview_cache.json` — a full-result cache (a hit makes no API call). Re-running a date with the same provider costs nothing; switching providers only bills the newly requested entries. Bump `OVERVIEW_CACHE_VERSION` when changing the prompt or generation params so stale entries are not reused. The Gemini call sets `thinkingConfig.thinkingBudget=0` and `maxOutputTokens=800` (Gemini 3.x thinking tokens otherwise consume the budget and truncate output). Calls are spaced `GEMINI_MIN_INTERVAL` (5s, sized to free-tier RPM); the primary model fails fast (1 attempt) and a per-run circuit breaker skips it after `GEMINI_PRIMARY_429_LIMIT` 429s — free-tier `gemini-3.5-flash` often exhausts its quota mid-run, so `gemini-3.1-flash-lite` is the practical workhorse. This keeps a full summary run ~2 min instead of ~12.

**NYT source routing** (`run_ranker`, `nyt_wsj_rss_ranker.py`): any backfill (news date in the past) uses the **NYT Archive API** as the article *source* so the digest reflects what was actually published that day — fetch the month, filter to the news date, score with the usual signals plus a print-front-page prominence bonus and a `type_of_material` filter. The daily cron (news date == today) uses **live RSS**. If Archive returns 0 docs (NYT index lag for very recent days), the code automatically falls back to RSS. The news date is the digest date (content + 1). Archive-sourced items carry `word_count` + `abstract` inline (no Article Search calls). Archive months cache forever under `output/ranker_diagnostics/nyt_archive/` (gitignored, ~11MB/month). Requires `NYT_API_KEY`; without it, backfill falls back to live RSS (current articles, wrong date).

For RSS-sourced (recent) NYT items, word counts come from the NYT Article Search API, not scraping (NYT returns 403 to the scraper). Filtering by `fq=web_url` returns zero results in this API version; instead the code derives a `q` query from the URL slug, searches, and exact-matches the returned `web_url` (see `_nyt_search_word_count`). Lookups are per-URL, throttled, cached, and degrade to scraping (then to nothing) when the key is missing or an article isn't found.

The **digest date** (title, output filenames, archive labels) is the content date + 1 — the day the digest represents (HN = previous day's top, NYT = that day's news); see `digest_date_for`. The header also shows `Fetched:` = compile timestamp (always now, even for backfill). HN archive and `dd_archive` still key off the content date.

## Comprehension Layer

`src/comprehension/` answers "what does this mean", where the digest answers "what
should I read". Its purpose is **fluency, not decision support** — it deliberately
does **not** read the Merge4 Obsidian vault. All reader context comes from
`config/reader_profile.md`, which is hand-owned and should hold only stable facts
(no dates, priorities, or headcount — those rot and produce confidently wrong framing).

The mechanism is continuity. Each day's items are filed into persistent **threads**
so a given day reads as an update to a story already understood. Threads are
LLM-discovered but *incrementally*: every run sees the existing registry and is
instructed to file into it by preference. Slugs are immutable once created; names
and charters may be revised; merged and dormant threads are retained with a status,
never deleted.

Module map, in dependency order:

- `paths.py` — every path, derived from `__file__`.
- `store.py` — thread registry (`data/comprehension/threads.json`), reading marks, and their pure operations. No network.
- `sources.py` — loads items from the digest JSON, or from archived `output/daily_md/digest_*.md` for backfill. Section headings have been renamed over the archive's life, so `_MD_SECTION_GROUPS` maps every historical variant; dropping one silently loses whole days.
- `llm.py` — Claude via the official `anthropic` SDK (not the digest's raw-HTTP pattern: the SDK already retries 429/5xx and can guarantee valid JSON via `output_config.format`). Adds a full-result cache and a per-run circuit breaker. **`temperature`/`top_p`/`top_k` are rejected with a 400 on these models**; steer by prompt. `max_tokens` caps thinking *and* text together.
- `triage.py` — one batched call per day: discover/file threads. Assignments must name a registry slug or a `new_threads` key; undeclared references are recovered as threads rather than dropped (they used to cost most of a day's filing).
- `narrative.py` — per-thread "what changed today / why it matters", persisted per date.
- `deepread.py` — fetches full text for the few promoted items and writes the `Context:` note. NYT/WSJ/FT are on `BLOCKED_HOSTS` (they 403 the scraper), and promotion prefers fetchable items so the budget is not spent where no text exists.
- `republish.py` — re-renders the digest with `Context:` folded in. Necessary because comprehension runs *after* the digest is written; if it fails, the already-written digest stands.
- `weekly.py` — one Opus essay over the trailing week (what happened / what was noise / what to watch).
- `render.py` — companion pages, weekly pages, and the comprehension archive. Reuses the digest's `_PAGE_CSS`/`_PAGE_SCRIPT`.
- `run.py` — the CLI, and the only module that orchestrates.

Models are configured under `settings.comprehension` in `config/config.json`:
Sonnet for the daily passes, Opus for the weekly essay.

**Two-track corpus.** `ai-at-large` (the AI conversation everyone is having) and
`ai-infra` (data-center power, rack electrical architecture, power semiconductors,
grid). The blog ranker tags sources via `track_for_source`, and `select_by_track`
gives `ai-infra` its own link allocation so the chattier general-AI feeds cannot
crowd it out. Every `ai-infra` feed in `DEFAULT_SOURCES` was verified to return
dated entries — a feed whose entries carry no dates falls back to the first-seen
cache heuristic and can flood the digest.

**Reading marks** are the feedback loop, and they attach to *explanations* — not to
every headline. Three buttons (`knew this` / `useful` / `over my head`) appear:

- **per thread on the companion page** — the primary surface, ~10 a day. A thread
  is a thing that was explained, so the mark says whether the explanation landed.
- **per item in the digest, only on items carrying a `Context:` note** — ~3 a day.

An earlier version put marks on all ~67 digest items. That was worse on both counts:
the target was ambiguous (was the *headline* unclear, or the article, or nothing?),
and 67 rows of buttons is how a feedback loop dies. Thread keys are namespaced
`thread:<slug>` (`store.thread_mark_key`) so they cannot collide with item URLs, and
`store.labels_by_mark_key` resolves both scopes, listing threads first in the
altitude hint.

Both surfaces share one script: `_PAGE_SCRIPT` binds to `.marks[data-mark-key]` and
dims `closest('.article, .thread')`, so it works on the digest and the companion page
without duplication. Marks persist in `localStorage`, export via the toolbar's
**Save marks** button as `~/Downloads/dd-marks-<date>.json`, and the next run harvests
and archives them. The export carries every mark ever made and the merge is
idempotent, so a skipped day loses nothing. Silence must stay safe: no marks means no
change, never a demotion.

## Path Conventions

Do not hardcode the checkout path. Python modules should derive paths from `Path(__file__).resolve()` and shell scripts should derive paths from their script directory.

`scripts/com.michelle.dailydigest.plist` is a template. `scripts/setup_launchagent.sh` fills in the current repo path when installing it to `~/Library/LaunchAgents/`.

## Safety Notes

The main digest should preserve existing digest/index/archive files when network fetches fail or no real content is available.

**Running the digest from a git worktree will try to destroy the archives.** `data/hn/hn_archive.md`, `dd_archive.md`, and both `.xlsx` files are regenerated wholesale from `*_archive_data.json`, which is gitignored and therefore absent in a worktree — so the rebuild reconstructs a hundred-day table from a single day. `_archive_would_shrink` now refuses any rebuild that would discard more than half the existing table, which covers the missing-JSON, partial-JSON, and corrupt-JSON cases alike. If you see "preserved the existing archive" in a log, that guard fired and the archive JSON is thin — expected in a worktree, worth investigating in the real checkout.

Reddit cleanup defaults to dry-run. Account-changing behavior requires Reddit OAuth environment variables and explicit `--execute` flags.

Generated outputs and large caches are part of the local workflow. Avoid deleting data under `data/` or `output/` unless the user explicitly asks.

Before changing scheduler behavior, verify both `scripts/run_digest.sh` and `scripts/setup_launchagent.sh`. The plist invokes `run_digest.sh`, so adding a step inside that script needs no installer change.

The comprehension pass (`[3/4]`) must never fail the run: the digest's outputs are already written by then, and the script returns the digest's exit code, not the comprehension pass's. The weekly synthesis runs on Sundays inside the same script — delete that block to make it manual-only.

The comprehension layer must not write into the Merge4 Obsidian vault. It is a reading tool; the vault has its own create-gate conventions and is out of scope by design.
