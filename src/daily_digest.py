#!/usr/bin/env python3
"""
Daily Digest - personal news digest generator.

Outputs:
  - daily_html/digest_YYYY-MM-DD.html
  - daily_md/digest_YYYY-MM-DD.md
  - index.html
  - hn_archive.md / hn_archive_data.json
  - dd_archive.md / dd_archive_data.json
"""

import datetime
import html
import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from urllib.parse import urljoin

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

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
RANKERS_DIR = SCRIPT_DIR / "rankers"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(RANKERS_DIR) not in sys.path:
    sys.path.insert(0, str(RANKERS_DIR))

try:
    from src.rankers.nyt_wsj_rss_ranker import run_ranker as run_nyt_wsj_ranker
    HAS_NYT_WSJ_RANKER = True
except Exception:
    HAS_NYT_WSJ_RANKER = False
    run_nyt_wsj_ranker = None

try:
    from src.rankers.blog_reading_ranker import run_ranker as run_blog_ranker
    HAS_BLOG_RANKER = True
except Exception:
    HAS_BLOG_RANKER = False
    run_blog_ranker = None


CONFIG_FILE = REPO_ROOT / "config" / "config.json"
DAILY_HTML_DIR = REPO_ROOT / "output" / "daily_html"
DAILY_MD_DIR = REPO_ROOT / "output" / "daily_md"
RANKER_OUTPUT_DIR = REPO_ROOT / "output" / "ranker_diagnostics"
HN_DATA_DIR = REPO_ROOT / "data" / "hn"
DIGEST_ARCHIVE_DIR = REPO_ROOT / "data" / "digest_archives"
DIGEST_ARCHIVE_PAGE = REPO_ROOT / "digest_archive.html"

DEFAULT_CONFIG = {
    "settings": {
        "hn_digest_count": 16,
        "nyt_wsj_max_links": 20,
        "blog_max_links": 20,
        "ranker_output_dir": "output/ranker_diagnostics"
    },
    "github_pages": {
        "enabled": False,
        "_setup": "Run: bash setup_github_pages.sh - then set enabled to true"
    }
}

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
HN_COMPANION_BASE_URL = "https://app.hncompanion.com"
ARTICLE_OVERVIEW_CACHE_FILE = RANKER_OUTPUT_DIR / "article_overview_cache.json"
READING_STATS_CACHE_FILE = RANKER_OUTPUT_DIR / "article_reading_stats_cache.json"
READING_WORDS_PER_MINUTE = 230

NYT_ARTICLE_SEARCH_URL = "https://api.nytimes.com/svc/search/v2/articlesearch.json"
NYT_API_MIN_INTERVAL = 6.0
NYT_API_MAX_ATTEMPTS = 4
NYT_API_BACKOFF_BASE = 4.0

SUMMARY_PROVIDERS = ("gemini", "claude-sonnet", "claude-opus", "none")
PREMIUM_SUMMARY_PROVIDERS = {"claude-sonnet", "claude-opus"}
DEFAULT_SUMMARY_MODELS = {
    "gemini": "gemini-3.5-flash",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-opus": "claude-opus-4-7",
}

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"
# Free-tier RPM is low, so space calls ~5s (probe: flash-lite is reliable at 5s).
GEMINI_MIN_INTERVAL = 5.0
GEMINI_BACKOFF_BASE = 2.0
# Primary model fails fast (1 try) -> fall back immediately; the final fallback
# model is the workhorse and gets real retries.
GEMINI_PRIMARY_ATTEMPTS = 1
GEMINI_FALLBACK_ATTEMPTS = 3
# Circuit breaker: once the primary 429s this many times in a run, skip it for the
# rest of the run (free-tier flash often exhausts its daily quota mid-run).
GEMINI_PRIMARY_429_LIMIT = 2
_gemini_primary_strikes = 0

NYT_FEEDS = [
    ("U.S.", "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    ("Business", "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Opinion", "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/Opinion.xml"),
    ("Lifestyle", "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/FashionandStyle.xml"),
]

WSJ_FEEDS = [
    ("U.S.", "WSJ", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("Business", "WSJ", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("Opinion", "WSJ", "https://feeds.a.dj.com/rss/RSSOpinion.xml"),
    ("Lifestyle", "WSJ", "https://feeds.a.dj.com/rss/RSSWSJ.xml"),
]

MIT_RSS = {
    "MIT IDE": ["https://ide.mit.edu/feed/"],
    "MIT Shaping Work": ["https://shapingwork.mit.edu/feed/"],
}

MIT_SCRAPE = {
    "MIT IDE": "https://ide.mit.edu/latest-insights/",
    "MIT Shaping Work": "https://shapingwork.mit.edu/research/",
}

BLOG_FEEDS = [
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Shkspr.mobi", "https://shkspr.mobi/blog/feed/"),
    ("Dan Luu", "https://danluu.com/atom.xml"),
    ("Daring Fireball", "https://daringfireball.net/feeds/main"),
    ("Tonsky.me", "https://tonsky.me/blog/atom.xml"),
    ("Troy Hunt", "https://feeds.feedburner.com/TroyHunt"),
    ("Lemire.me", "https://lemire.me/blog/feed/"),
    ("Gwern.net", "https://gwern.net/feed/daily"),
]

OPINION_SIGNALS = (
    "opinion", "editorial", "column", "commentary", "perspective", "essay",
    "review", "the case for", "the case against", "analysis"
)

SECTION_TOPICS = {
    "U.S.": "U.S. News",
    "Business": "Business",
    "Opinion": "Opinion",
    "Lifestyle": "Lifestyle",
}

SOURCE_TOPICS = {
    "HackerNews": "Technology",
    "MIT IDE": "Research",
    "MIT Shaping Work": "Research",
    "Krebs on Security": "Security",
    "Troy Hunt": "Security",
    "Simon Willison": "Technology",
    "Dan Luu": "Technology",
    "Tonsky.me": "Technology",
    "Paul Graham": "Technology",
    "Gwern.net": "Technology",
    "Lemire.me": "Technology",
    "Neal.fun": "Technology",
    "Daring Fireball": "Strategy",
    "Shkspr.mobi": "Strategy",
    "LinkedIn": "Business",
}

BLOG_SECURITY = {"Krebs on Security", "Troy Hunt"}
BLOG_TECH = {
    "Simon Willison", "Dan Luu", "Tonsky.me", "Paul Graham",
    "Gwern.net", "Lemire.me", "Neal.fun"
}
BLOG_STRATEGY = {
    "Daring Fireball", "Shkspr.mobi"
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"  [Config] Error reading config.json: {e}; using defaults.")
            return DEFAULT_CONFIG
        config.setdefault("settings", {})
        for key, value in DEFAULT_CONFIG["settings"].items():
            config["settings"].setdefault(key, value)
        config.setdefault("github_pages", DEFAULT_CONFIG["github_pages"])
        return config
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    return DEFAULT_CONFIG


def get_yesterday():
    return datetime.date.today() - datetime.timedelta(days=1)


def digest_date_for(content_date):
    # The digest represents the day after its content date: HN = yesterday's top,
    # NYT = that day's news. Title, filenames, and archive labels use this date.
    return content_date + datetime.timedelta(days=1)


def content_date_from_user_input(value):
    try:
        input_date = datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format.") from exc
    return input_date - datetime.timedelta(days=1)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Generate Daily Digest. By default, fetches yesterday's content. "
            "--date YYYY-MM-DD fetches/ranks articles from previous day."
        )
    )
    parser.add_argument(
        "--date",
        help="User input date in YYYY-MM-DD; digest content date becomes previous day.",
    )
    parser.add_argument(
        "--model", "--summary-provider",
        dest="summary_provider",
        choices=SUMMARY_PROVIDERS,
        default="none",
        help=(
            "Model family for article overviews. Default: none (abstract only, no LLM). "
            "Pass --model gemini to generate overviews with gemini-3.5-flash (falls back "
            "to gemini-3.1-flash-lite); claude-sonnet / claude-opus also available."
        ),
    )
    parser.add_argument(
        "--model-id", "--summary-model",
        dest="summary_model",
        help="Override the exact model id for the selected --model family.",
    )
    parser.add_argument(
        "--sections", "--summary-nyt-sections",
        dest="summary_nyt_sections",
        help=(
            "For claude-sonnet/claude-opus: comma-separated NYT digest sections to "
            "summarize with Claude, or 'all'. Other NYT sections use Gemini."
        ),
    )
    return parser.parse_args(argv)


def _parse_section_filter(value):
    if not value:
        return set()
    if value.strip().lower() == "all":
        return "all"
    return {part.strip() for part in re.split(r"[,;]", value) if part.strip()}


def summary_config_from_args(args):
    provider = args.summary_provider
    if provider in PREMIUM_SUMMARY_PROVIDERS and not args.summary_nyt_sections:
        raise ValueError("--summary-nyt-sections is required when using Claude summary providers.")
    return {
        "provider": provider,
        "model": args.summary_model or DEFAULT_SUMMARY_MODELS.get(provider, ""),
        "nyt_sections": _parse_section_filter(args.summary_nyt_sections),
    }


def article_summary_provider(article, group, config):
    provider = config.get("provider", "none")
    if provider == "none":
        return None
    if group == "blogs":
        return provider
    if group == "nyt_wsj":
        # NYT always shows its RSS abstract (fill_nyt_abstracts); the LLM overview
        # here is the richer, opt-in analytical summary shown alongside it.
        if provider == "gemini":
            return "gemini"
        sections = config.get("nyt_sections") or set()
        section = article.get("topic_tag") or article.get("section") or ""
        if sections == "all" or section in sections:
            return provider
        return "gemini"
    return None


def unix_range(date):
    start = datetime.datetime.combine(date, datetime.time.min)
    end = datetime.datetime.combine(date, datetime.time.max)
    return int(start.timestamp()), int(end.timestamp())


def article_key(article):
    url = (article.get("url") or "").strip().rstrip("/")
    title = (article.get("title") or "").strip().lower()
    return url or title


def dedupe_articles(articles):
    seen = set()
    out = []
    for article in articles:
        key = article_key(article)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(article)
    return out


def _rss_date(entry):
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value:
            try:
                return datetime.date(value.tm_year, value.tm_mon, value.tm_mday)
            except Exception:
                pass
    return None


def _clean_summary(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def reading_time_minutes(word_count, words_per_minute=READING_WORDS_PER_MINUTE):
    try:
        count = int(word_count)
    except (TypeError, ValueError):
        return 0
    if count <= 0:
        return 0
    return max(1, (count + words_per_minute - 1) // words_per_minute)


def _word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def article_word_count_from_html(html_text):
    if not html_text:
        return 0
    if HAS_BS4:
        soup = BeautifulSoup(html_text, "html.parser")
        for node in soup(["script", "style", "noscript", "svg", "form", "nav", "header", "footer"]):
            node.decompose()
        article = soup.find("article")
        text = (article or soup.body or soup).get_text(" ", strip=True)
        return _word_count(text)
    cleaned = re.sub(r"<(script|style|noscript|svg|form|nav|header|footer)\b.*?</\1>", " ", html_text, flags=re.I | re.S)
    return _word_count(_clean_summary(cleaned))


def _reading_stats_cache_key(article):
    key = article_key(article)
    title = (article.get("title") or "").strip().lower()
    return f"{key}|{title}"


def _reading_meta_text(article):
    word_count = article.get("word_count")
    minutes = article.get("reading_time_minutes")
    if not word_count or not minutes:
        return ""
    try:
        word_count = int(word_count)
        minutes = int(minutes)
    except (TypeError, ValueError):
        return ""
    return f"{word_count:,} words · {minutes} min read"


def _normalize_url(url):
    url = (url or "").strip()
    url = url.split("?", 1)[0].split("#", 1)[0]
    return url.rstrip("/").lower()


_LAST_NYT_CALL = 0.0


def _throttle_nyt():
    global _LAST_NYT_CALL
    elapsed = time.monotonic() - _LAST_NYT_CALL
    if elapsed < NYT_API_MIN_INTERVAL:
        time.sleep(NYT_API_MIN_INTERVAL - elapsed)
    _LAST_NYT_CALL = time.monotonic()


def _nyt_slug_query(url):
    slug = _normalize_url(url).rsplit("/", 1)[-1]
    if slug.endswith(".html"):
        slug = slug[:-5]
    return slug.replace("-", " ").strip()


def _nyt_search_word_count(url, api_key):
    # fq=web_url filtering returns zero results in this API version, so search by a
    # slug-derived q and exact-match the returned web_url instead.
    query = _nyt_slug_query(url)
    if not query:
        return None
    target = _normalize_url(url)
    last_error = None
    for attempt in range(NYT_API_MAX_ATTEMPTS):
        _throttle_nyt()
        response = requests.get(
            NYT_ARTICLE_SEARCH_URL,
            params={"q": query, "fl": "web_url,word_count", "api-key": api_key},
            timeout=20,
        )
        if response.status_code in (429, 503):
            last_error = requests.HTTPError(f"{response.status_code} from NYT API")
            if attempt < NYT_API_MAX_ATTEMPTS - 1:
                wait = _retry_after_seconds(response)
                if wait is None:
                    wait = NYT_API_BACKOFF_BASE * (2 ** attempt)
                time.sleep(wait)
                continue
            raise last_error
        response.raise_for_status()
        docs = ((response.json() or {}).get("response") or {}).get("docs") or []
        for doc in docs:
            if _normalize_url(doc.get("web_url")) == target:
                return doc.get("word_count") or None
        return None
    if last_error:
        raise last_error
    return None


def fetch_nyt_word_counts(urls):
    api_key = os.environ.get("NYT_API_KEY")
    urls = list(dict.fromkeys(url for url in urls if url))
    if not api_key or not urls:
        return {}
    counts = {}
    for url in urls:
        try:
            count = _nyt_search_word_count(url, api_key)
        except Exception as e:
            print(f"  [Reading stats] NYT API error: {e}")
            break
        if count:
            counts[_normalize_url(url)] = count
    return counts


def fetch_article_reading_stats(article):
    url = article.get("url")
    if not url:
        return {}
    response = _request_get(url, "Reading stats", timeout=20)
    if response is None:
        return {}
    count = article_word_count_from_html(response.text)
    if not count:
        return {}
    return {"word_count": count, "reading_time_minutes": reading_time_minutes(count)}


def enrich_article_reading_stats(data, cache=None, cache_path=None):
    cache_path = cache_path or READING_STATS_CACHE_FILE
    cache = cache if cache is not None else _load_json_file(cache_path, {})
    changed = False
    cached = fetched = missing = 0

    pending_nyt_urls = []
    for group in ("hn", "nyt_wsj", "blogs", "linkedin"):
        for article in data.get(group, []):
            if article.get("word_count") and article.get("reading_time_minutes"):
                continue
            if cache.get(_reading_stats_cache_key(article)):
                continue
            url = article.get("url") or ""
            if "nytimes.com" in url:
                pending_nyt_urls.append(url)
    nyt_word_counts = fetch_nyt_word_counts(pending_nyt_urls)

    for group in ("hn", "nyt_wsj", "blogs", "linkedin"):
        for article in data.get(group, []):
            if article.get("word_count") and article.get("reading_time_minutes"):
                continue
            key = _reading_stats_cache_key(article)
            if cache.get(key):
                article.update(cache[key])
                cached += 1
                continue
            count = nyt_word_counts.get(_normalize_url(article.get("url")))
            if count:
                stats = {"word_count": count, "reading_time_minutes": reading_time_minutes(count)}
            else:
                stats = fetch_article_reading_stats(article)
            if stats:
                article.update(stats)
                cache[key] = stats
                fetched += 1
                changed = True
            else:
                missing += 1
    if changed:
        _write_json_file(cache_path, cache)
    print(f"  [Reading stats] Cached: {cached}; fetched: {fetched}; missing: {missing}.")
    return data


def _is_opinion(title, section):
    if (section or "").lower() == "opinion":
        return True
    lower = (title or "").lower()
    return any(signal in lower for signal in OPINION_SIGNALS)


def _infer_topic(article):
    if article.get("topic"):
        return article["topic"]
    if article.get("outlet") == "HN":
        return "Technology"
    section = article.get("section")
    if section in SECTION_TOPICS:
        return SECTION_TOPICS[section]
    source = article.get("source")
    if source in SOURCE_TOPICS:
        return SOURCE_TOPICS[source]
    category = article.get("category")
    return {
        "news": "News",
        "opinion": "Opinion",
        "long-form": "Technology",
        "research": "Research",
        "social": "Business",
        "tech": "Technology",
    }.get(category, "General")


def _request_get(url, source, timeout=15):
    if not HAS_REQUESTS:
        print(f"  [{source}] Error: requests is not installed.")
        return None
    try:
        response = requests.get(url, timeout=timeout, headers=HTTP_HEADERS)
        response.raise_for_status()
        return response
    except Exception as e:
        print(f"  [{source}] Error: {e}")
        return None


def hn_companion_url(item_id):
    return f"{HN_COMPANION_BASE_URL}/item?id={item_id}"


def extract_hn_companion_overview(summary):
    if not summary:
        return ""
    match = re.search(r"^# Overview\s*\n(?P<body>.*?)(?=\n# |\Z)", summary, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    overview = match.group("body").strip()
    overview = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", overview)
    overview = re.sub(r"[*_`>#-]+", "", overview)
    return re.sub(r"\s+", " ", overview).strip()


def fetch_hn_companion_overview(item_id):
    if not HAS_REQUESTS:
        return ""
    try:
        response = requests.get(
            f"{HN_COMPANION_BASE_URL}/api/posts/{item_id}",
            timeout=12,
            headers=HTTP_HEADERS,
        )
        if response.status_code == 404:
            return ""
        response.raise_for_status()
    except Exception as e:
        print(f"  [HN Companion] Error fetching summary for {item_id}: {e}")
        return ""
    try:
        return extract_hn_companion_overview(response.json().get("summary", ""))
    except Exception as e:
        print(f"  [HN Companion] Error parsing summary for {item_id}: {e}")
        return ""


def _load_json_file(path, default):
    path = Path(path)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [Summaries] Error reading cache {path.name}: {e}")
    return default


def _write_json_file(path, value):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as e:
        print(f"  [Summaries] Error writing cache {path.name}: {e}")


# Bump when generation params/prompt/cache-format change so stale entries are not reused.
OVERVIEW_CACHE_VERSION = "v3"

MODEL_DISPLAY_NAMES = {
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-opus-4-7": "Claude Opus 4.7",
}


def _model_display_name(model):
    if not model:
        return ""
    if model in MODEL_DISPLAY_NAMES:
        return MODEL_DISPLAY_NAMES[model]
    return model.replace("-", " ").title()


def _article_overview_cache_key(article, provider, model):
    key = article_key(article)
    title = (article.get("title") or "").strip().lower()
    return f"{OVERVIEW_CACHE_VERSION}|{provider}|{model}|{key}|{title}"


def _article_overview_prompt(article):
    source = article.get("outlet") or article.get("source") or "Unknown source"
    section = article.get("topic_tag") or article.get("section") or article.get("topic") or ""
    return (
        "Write one concise, high-quality overview for this reading-list item.\n"
        "Target 60-90 words. One paragraph. No markdown. Do not invent facts beyond provided metadata. "
        "Explain what it covers and why it matters.\n\n"
        f"Title: {article.get('title', '')}\n"
        f"Source: {source}\n"
        f"Section: {section}\n"
        f"URL: {article.get('url', '')}\n"
        f"Feed summary: {article.get('summary', '')}\n"
        f"Ranking reason: {article.get('reason', '')}\n"
        f"Reading mode: {article.get('reading_mode', '')}\n"
    )


def _clean_generated_overview(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^overview:\s*", "", text, flags=re.IGNORECASE)
    return text.strip(" -*_`")


_LAST_GEMINI_CALL = 0.0


def _retry_after_seconds(response):
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _throttle_gemini():
    global _LAST_GEMINI_CALL
    elapsed = time.monotonic() - _LAST_GEMINI_CALL
    if elapsed < GEMINI_MIN_INTERVAL:
        time.sleep(GEMINI_MIN_INTERVAL - elapsed)
    _LAST_GEMINI_CALL = time.monotonic()


def _gemini_generate(prompt, model, api_key, max_attempts=GEMINI_FALLBACK_ATTEMPTS):
    last_error = None
    for attempt in range(max_attempts):
        _throttle_gemini()
        response = requests.post(
            f"{GEMINI_API_BASE}/{model}:generateContent",
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 800,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=45,
        )
        if response.status_code in (429, 503):
            last_error = requests.HTTPError(f"{response.status_code} from {model}")
            if attempt < max_attempts - 1:
                wait = _retry_after_seconds(response)
                if wait is None:
                    wait = GEMINI_BACKOFF_BASE * (2 ** attempt)
                time.sleep(wait)
                continue
            raise last_error
        response.raise_for_status()
        parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return _clean_generated_overview(" ".join(part.get("text", "") for part in parts))
    if last_error:
        raise last_error
    return ""


def _call_gemini_summary(prompt, model):
    global _gemini_primary_strikes
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "", ""
    models = [model]
    if GEMINI_FALLBACK_MODEL not in models:
        models.append(GEMINI_FALLBACK_MODEL)
    # Circuit breaker: skip an exhausted primary for the rest of the run.
    if len(models) > 1 and _gemini_primary_strikes >= GEMINI_PRIMARY_429_LIMIT:
        models = models[1:]
    last_error = None
    for index, candidate in enumerate(models):
        is_final = index == len(models) - 1
        attempts = GEMINI_FALLBACK_ATTEMPTS if is_final else GEMINI_PRIMARY_ATTEMPTS
        try:
            return _gemini_generate(prompt, candidate, api_key, attempts), candidate
        except Exception as e:
            last_error = e
            if not is_final:
                _gemini_primary_strikes += 1
                print(f"  [Summaries] gemini {candidate} unavailable ({e}); falling back to {models[index + 1]}.")
    if last_error:
        raise last_error
    return "", ""


def _call_claude_summary(prompt, model):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "", ""
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": model,
            "max_tokens": 220,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=45,
    )
    response.raise_for_status()
    chunks = [part.get("text", "") for part in response.json().get("content", []) if part.get("type") == "text"]
    return _clean_generated_overview(" ".join(chunks)), model


def generate_article_overview(article, provider, model):
    """Return (overview_text, actual_model_id)."""
    if not HAS_REQUESTS:
        return "", ""
    prompt = _article_overview_prompt(article)
    if provider == "gemini":
        return _call_gemini_summary(prompt, model)
    if provider in PREMIUM_SUMMARY_PROVIDERS:
        return _call_claude_summary(prompt, model)
    return "", ""


def enrich_article_overviews(data, config, settings=None, cache_path=None):
    global _gemini_primary_strikes
    if config.get("provider") == "none":
        print("  [Summaries] Disabled.")
        return data
    _gemini_primary_strikes = 0

    settings = settings or DEFAULT_CONFIG["settings"]
    cache_path = cache_path or ARTICLE_OVERVIEW_CACHE_FILE
    cache = _load_json_file(cache_path, {})
    changed = False
    cached = generated = missing = 0

    for group, articles in (("nyt_wsj", data.get("nyt_wsj", [])), ("blogs", data.get("blogs", []))):
        for article in articles:
            if article.get("article_overview"):
                continue
            provider = article_summary_provider(article, group, config)
            if not provider:
                continue
            model = config.get("model") if provider == config.get("provider") else DEFAULT_SUMMARY_MODELS[provider]
            key = _article_overview_cache_key(article, provider, model)
            entry = cache.get(key)
            if entry:
                article["article_overview"] = entry.get("text", "")
                article["overview_model"] = _model_display_name(entry.get("model"))
                cached += 1
                continue
            try:
                overview, used_model = generate_article_overview(article, provider, model)
            except Exception as e:
                print(f"  [Summaries] {provider} error for {article.get('title', '')[:70]}: {e}")
                overview, used_model = "", ""
            if overview:
                article["article_overview"] = overview
                article["overview_model"] = _model_display_name(used_model)
                cache[key] = {"text": overview, "model": used_model}
                generated += 1
                changed = True
            else:
                missing += 1

    if changed:
        _write_json_file(cache_path, cache)
    print(f"  [Summaries] Cached: {cached}; generated: {generated}; missing: {missing}.")
    return data


def fill_nyt_abstracts(data):
    filled = 0
    for article in data.get("nyt_wsj", []):
        if article.get("abstract"):
            continue
        abstract = re.sub(r"\s+", " ", article.get("summary") or "").strip()
        if abstract:
            article["abstract"] = abstract
            filled += 1
    print(f"  [NYT abstracts] Filled: {filled}.")
    return data


def fetch_hackernews(n=12, date=None, verbose=False):
    if date is None:
        date = get_yesterday()
    start, end = unix_range(date)
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?tags=story&hitsPerPage=1000"
        f"&numericFilters=created_at_i>{start},created_at_i<{end}"
    )
    if verbose:
        print(f"  [HN] Fetching Algolia stories for {date.isoformat()}...")
    response = _request_get(url, "HN", timeout=20)
    if response is None:
        return []
    try:
        hits = response.json().get("hits", [])
        if verbose:
            print(f"  [HN] Raw hits: {len(hits):,}")
        stories = []
        for hit in hits:
            object_id = hit.get("objectID")
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            stories.append({
                "title": title,
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
                "hn_url": f"https://news.ycombinator.com/item?id={object_id}",
                "object_id": object_id,
                "score": hit.get("points", 0) or 0,
                "comments": hit.get("num_comments", 0) or 0,
                "author": hit.get("author", ""),
                "source": "HackerNews",
                "category": "tech",
                "outlet": "HN",
                "section": None,
                "date": date,
            })
        selected = sorted(dedupe_articles(stories), key=lambda item: item["score"], reverse=True)[:n]
        if verbose:
            print(f"  [HN] Ranked stories: {len(stories):,}; selected top {len(selected)}.")
        for story in selected:
            object_id = story.get("object_id")
            if object_id:
                story["hn_companion_url"] = hn_companion_url(object_id)
                story["discussion_overview"] = fetch_hn_companion_overview(object_id)
        if verbose:
            summarized = sum(1 for story in selected if story.get("discussion_overview"))
            missing = len(selected) - summarized
            print(f"  [HN Companion] Cached overviews: {summarized}; missing: {missing}.")
        return selected
    except Exception as e:
        print(f"  [HN] Error parsing response: {e}")
        return []


def fetch_rss(url, source, max_items=30):
    if not HAS_FEEDPARSER:
        print(f"  [RSS:{source}] Error: feedparser is not installed.")
        return []
    try:
        if HAS_REQUESTS:
            response = requests.get(url, timeout=20, headers=HTTP_HEADERS)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        else:
            feed = feedparser.parse(url, request_headers=HTTP_HEADERS)
        if getattr(feed, "bozo", False) and not getattr(feed, "entries", []):
            print(f"  [RSS:{source}] Error: {getattr(feed, 'bozo_exception', 'feed parse failed')}")
            return []
        articles = []
        for entry in feed.entries[:max_items]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            articles.append({
                "title": title,
                "url": link,
                "date": _rss_date(entry),
                "source": source,
                "summary": _clean_summary(entry.get("summary", ""))[:180],
                "category": "news",
                "outlet": None,
                "section": None,
                "is_fallback": False,
            })
        return dedupe_articles(articles)
    except Exception as e:
        print(f"  [RSS:{source}] Error: {e}")
        return []


def fetch_news(feeds, n_per_feed=12):
    articles = []
    for section, outlet, url in feeds:
        items = fetch_rss(url, f"{outlet} {section}", max_items=n_per_feed)
        for item in items:
            item["outlet"] = outlet
            item["section"] = section
            item["source"] = f"{outlet} {section}"
            item["category"] = "opinion" if _is_opinion(item["title"], section) else "news"
        articles.extend(items)
    return dedupe_articles(articles)


def _scrape_links(url, source, category, max_items=5, min_title_len=12):
    if not HAS_BS4:
        print(f"  [Scrape:{source}] Error: beautifulsoup4 is not installed.")
        return []
    response = _request_get(url, f"Scrape:{source}")
    if response is None:
        return []
    try:
        soup = BeautifulSoup(response.text, "html.parser")
        articles = []
        for tag in soup.find_all("a", href=True):
            title = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
            href = urljoin(url, tag["href"])
            if len(title) < min_title_len or href.startswith("mailto:"):
                continue
            lower = title.lower()
            if lower.startswith(("skip to", "explore ", "read more", "subscribe", "sign up")):
                continue
            if href.rstrip("/") == url.rstrip("/") or "#" in href:
                continue
            articles.append({
                "title": title,
                "url": href,
                "source": source,
                "category": category,
                "date": None,
                "outlet": None,
                "section": None,
                "is_fallback": True,
            })
        return dedupe_articles(articles)[:max_items]
    except Exception as e:
        print(f"  [Scrape:{source}] Error parsing response: {e}")
        return []


def fetch_mit_updates(since_date=None):
    articles = []
    for source, rss_urls in MIT_RSS.items():
        found = []
        for rss_url in rss_urls:
            items = fetch_rss(rss_url, source, max_items=12)
            dated = [item for item in items if item.get("date") == since_date] if since_date else items
            if dated:
                found = dated
                break
        if not found:
            found = _scrape_links(MIT_SCRAPE[source], source, "research", max_items=20, min_title_len=20)
            if source == "MIT IDE":
                found = [item for item in found if "/insights/" in item.get("url", "")]
            if source == "MIT Shaping Work":
                found = [item for item in found if "/research/" in item.get("url", "")]
        for item in found:
            item["category"] = "research"
        articles.extend(found[:4])
    return dedupe_articles(articles)


def fetch_linkedin_activity():
    return [{
        "title": "View Rama's recent LinkedIn activity",
        "url": "https://www.linkedin.com/in/ramar/recent-activity/all/",
        "source": "LinkedIn",
        "category": "social",
        "date": None,
        "outlet": None,
        "section": None,
        "is_fallback": False,
    }]


def _scrape_paulgraham():
    return _scrape_links(
        "http://paulgraham.com/articles.html",
        "Paul Graham",
        "long-form",
        max_items=2,
        min_title_len=8,
    )


def _scrape_neal_fun():
    posts = _scrape_links(
        "https://neal.fun/",
        "Neal.fun",
        "tech",
        max_items=20,
        min_title_len=5,
    )
    blocked = ("support", "about", "privacy", "shop", "newsletter")
    return [
        post for post in posts
        if "neal.fun" in post.get("url", "")
        and not any(part in post.get("url", "").lower() for part in blocked)
        and "coffee" not in post.get("title", "").lower()
    ][:2]


def _pick_blog_posts(source, url, target_date):
    posts = fetch_rss(url, source, max_items=20)
    exact = [post for post in posts if post.get("date") == target_date]
    chosen = exact[:2]
    if not chosen:
        chosen = posts[:2]
        for post in chosen:
            post["is_fallback"] = True
    for post in chosen:
        post["category"] = "tech" if source == "Neal.fun" else "long-form"
    return chosen


def fetch_blog_updates(since_date=None):
    target_date = since_date or get_yesterday()
    articles = []
    for source, url in BLOG_FEEDS:
        articles.extend(_pick_blog_posts(source, url, target_date))
    paul = _scrape_paulgraham()
    for post in paul:
        post["is_fallback"] = True
    articles.extend(paul[:1])
    articles.extend(_scrape_neal_fun()[:2])
    return dedupe_articles(articles)


def _split_news(articles):
    return (
        [item for item in articles if item.get("category") == "opinion"],
        [item for item in articles if item.get("category") != "opinion"],
    )


def build_sections(data, settings):
    hn_count = int(settings.get("hn_digest_count", 16))
    blog_items = data.get("blogs", [])
    research_sources = {"MIT IDE", "MIT Shaping Work"}
    return {
        "hn": data.get("hn", [])[:hn_count],
        "nyt_wsj": data.get("nyt_wsj", []),
        "research": [item for item in blog_items if item.get("source") in research_sources],
        "blogs": [item for item in blog_items if item.get("source") not in research_sources],
        "linkedin": data.get("linkedin", []),
    }


def _display_date(article):
    pub = article.get("date")
    day = None
    if isinstance(pub, datetime.date):
        day = pub
        label = pub.isoformat()
    elif pub:
        label = str(pub)
        try:
            day = datetime.date.fromisoformat(label[:10])
        except ValueError:
            day = None
    else:
        return ""
    if article.get("is_fallback"):
        return f"latest from {label}"
    if article.get("outlet") == "NYT":
        weekday = day.strftime("%a") if day else ""
        return f"Published: {weekday}, {label}" if weekday else f"Published: {label}"
    return label


def _html_escape(value):
    return html.escape(str(value or ""), quote=True)


def _md_escape(value):
    return str(value or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")


def _table_escape(value):
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()


def _xlsx_cell_ref(row, col):
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


def _xlsx_cell(value, row, col):
    ref = _xlsx_cell_ref(row, col)
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", str(value))
    text = xml_escape(text, {'"': "&quot;"})
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def write_xlsx(path, sheet_name, headers, rows):
    safe_sheet = xml_escape(sheet_name[:31] or "Archive", {'"': "&quot;"})
    all_rows = [headers] + rows
    col_count = max((len(row) for row in all_rows), default=len(headers))
    row_xml = []
    for row_index, row in enumerate(all_rows, 1):
        cells = "".join(_xlsx_cell(value, row_index, col_index) for col_index, value in enumerate(row, 1))
        row_xml.append(f'<row r="{row_index}">{cells}</row>')
    cols = "".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate([14, 14, 8, 60, 12, 12, 16, 80][:col_count], 1)
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{cols}</cols>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f'<autoFilter ref="A1:{_xlsx_cell_ref(len(all_rows), col_count)}"/>'
        '</worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe_sheet}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:creator>Daily Digest</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        '</cp:coreProperties>'
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>Daily Digest</Application>'
        '</Properties>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)


# ───────────────────────── HTML emission (editorial layout) ─────────────────────────
# These helpers + generate_html() emit the editorial / newspaper-style HTML that
# matches Daily Digest.html: 700px column, Lora serif / Inter sans toggle, DM Sans
# meta line, per-section accent colors, collapsible <details> sections + per-article
# Overview/Abstract panels, sticky toolbar with font / theme / expand-collapse toggles.

_PAGE_CSS = """:root {
  --bg: #f6f6f0;
  --paper: #fbf9f3;
  --ink: #14130f;
  --ink-soft: #2f2c26;
  --mute: #6a6760;
  --faint: #a09a8e;
  --rule: rgba(20, 19, 15, 0.14);
  --rule-soft: rgba(20, 19, 15, 0.07);
  --accent: #8a1a1a;
  --hover: #8a1a1a;
  --font-body: 'Lora', 'Iowan Old Style', Georgia, serif;
  --font-meta: 'DM Sans', ui-sans-serif, system-ui, -apple-system, sans-serif;
  --col: 700px;
  --body-size: 13px;
  --row-pad: 10px;
}

[data-theme="dark"] {
  --bg: #111216;
  --paper: #1a1b20;
  --ink: #b5b5b5;
  --ink-soft: #9e9e9e;
  --mute: #7a7a7a;
  --faint: #5a5a5a;
  --rule: rgba(181, 181, 181, 0.20);
  --rule-soft: rgba(181, 181, 181, 0.10);
  --accent: #d68c8c;
  --hover: #d68c8c;
}

[data-font="sans"] { --font-body: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif; }

* { box-sizing: border-box; }
html { background: var(--bg); scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font-body);
  font-size: var(--body-size);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  transition: background-color 180ms ease, color 180ms ease;
}
[data-font="sans"] body { letter-spacing: -0.005em; line-height: 1.52; }
::selection { background: var(--accent); color: var(--paper); }
a { color: inherit; text-decoration: none; }

/* Layout */
.page { max-width: var(--col); margin: 0 auto; padding: 24px 24px 56px; }

/* Toolbar */
.toolbar {
  position: sticky; top: 0; z-index: 100;
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 10px 0; margin: 0 0 4px;
  border-bottom: 1px solid var(--rule-soft);
  font-family: var(--font-meta);
  font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--mute);
  background: var(--bg);
  background: color-mix(in srgb, var(--bg) 92%, transparent);
  backdrop-filter: saturate(140%) blur(8px);
  -webkit-backdrop-filter: saturate(140%) blur(8px);
}
.toolbar a { color: var(--mute); transition: color 120ms ease; }
.toolbar a:hover { color: var(--ink); }
.toolbar a.archive { letter-spacing: 0.06em; }
.controls { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.btn {
  background: transparent; border: 1px solid var(--rule); color: var(--mute);
  font: inherit; letter-spacing: inherit; text-transform: inherit;
  padding: 4px 9px; cursor: pointer; border-radius: 2px;
  transition: color 120ms ease, border-color 120ms ease, background-color 120ms ease;
}
.btn:hover { color: var(--ink); border-color: var(--ink-soft); }
.btn[aria-pressed="true"] { color: var(--ink); border-color: var(--ink-soft); background: var(--paper); }
.seg { display: inline-flex; border: 1px solid var(--rule); border-radius: 2px; overflow: hidden; }
.seg .btn { border: none; border-radius: 0; padding: 4px 9px; color: var(--mute); }
.seg .btn + .btn { border-left: 1px solid var(--rule); }
.seg .btn:hover { color: var(--ink); }
.seg .btn[aria-pressed="true"] { background: var(--ink); color: var(--paper); }

/* Masthead */
.masthead {
  text-align: left; padding: 28px 0 18px;
  border-bottom: 1px solid var(--rule); margin-bottom: 24px;
}
.masthead .kicker {
  font-family: var(--font-meta); font-size: 10.5px;
  letter-spacing: 0.22em; text-transform: uppercase;
  color: var(--mute); margin-bottom: 8px;
}
.wordmark {
  font-family: var(--font-body); font-weight: 600;
  font-size: clamp(30px, 5vw, 40px); line-height: 1.05;
  letter-spacing: -0.018em; margin: 0; color: var(--ink);
}
[data-font="sans"] .wordmark { font-weight: 600; letter-spacing: -0.028em; }
.fetched {
  font-family: var(--font-meta); font-size: 11px;
  letter-spacing: 0.06em; color: var(--mute); margin-top: 10px;
}

/* Per-section accents */
details.section#hn        { --accent: #b8501c; --hover: #b8501c; }
details.section#nyt       { --accent: #4a4a48; --hover: #4a4a48; }
details.section#research  { --accent: #2a6b66; --hover: #2a6b66; }
details.section#blogs     { --accent: #4f6b3e; --hover: #4f6b3e; }
details.section#linkedin  { --accent: #2a5a8a; --hover: #2a5a8a; }
[data-theme="dark"] details.section#hn        { --accent: #e09b6b; --hover: #e09b6b; }
[data-theme="dark"] details.section#nyt       { --accent: #9a958a; --hover: #9a958a; }
[data-theme="dark"] details.section#research  { --accent: #7fb8b0; --hover: #7fb8b0; }
[data-theme="dark"] details.section#blogs     { --accent: #a8c08c; --hover: #a8c08c; }
[data-theme="dark"] details.section#linkedin  { --accent: #7ba6d4; --hover: #7ba6d4; }

/* Section */
.section { margin-top: 32px; scroll-margin-top: 60px; }
.section:first-of-type { margin-top: 8px; }
.section-head {
  margin-bottom: 14px; padding-bottom: 10px;
  border-bottom: 1px solid var(--rule);
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 16px; cursor: pointer; list-style: none; user-select: none;
  transition: color 120ms ease;
}
.section-head::-webkit-details-marker { display: none; }
.section-head:hover h2 { color: var(--accent); }
.section:not([open]) > .section-head { margin-bottom: 0; }
.section-head h2 {
  font-family: var(--font-body); font-weight: 500;
  font-size: 22px; line-height: 1.15; margin: 0;
  letter-spacing: -0.012em; transition: color 120ms ease; flex: 1;
  text-decoration: underline; text-decoration-style: dotted;
  text-decoration-thickness: 1.5px; text-underline-offset: 6px;
  text-decoration-color: color-mix(in srgb, var(--accent) 55%, transparent);
}
[data-font="sans"] .section-head h2 { font-weight: 600; letter-spacing: -0.022em; }
.section-head::after {
  content: "▾"; font-family: var(--font-meta); font-size: 11px;
  color: var(--accent); transition: transform 180ms ease;
  display: inline-block; transform-origin: center;
  flex-shrink: 0; margin-left: 12px; align-self: center;
}
.section:not([open]) > .section-head::after { transform: rotate(-90deg); }

/* Subsection */
.subsection { margin-top: 18px; }
.subsection:first-of-type { margin-top: 6px; }
.subhead {
  display: flex; align-items: center; gap: 10px;
  margin: 0 0 6px; cursor: pointer; list-style: none; user-select: none;
  transition: color 120ms ease;
}
.subhead::-webkit-details-marker { display: none; }
.subhead::before {
  content: "▾"; font-size: 9px; color: var(--mute);
  transition: transform 180ms ease;
  display: inline-block; transform-origin: center; flex-shrink: 0;
}
.subsection:not([open]) > .subhead::before { transform: rotate(-90deg); }
.subsection:not([open]) > .subhead { margin-bottom: 0; }
.subhead:hover h3 { color: var(--ink); }
.subhead::after {
  content: ""; flex: 1; height: 1px; background: var(--rule-soft);
}
.subhead h3 {
  font-family: var(--font-meta); font-size: 10.5px;
  font-weight: 500; letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--ink-soft); margin: 0; transition: color 120ms ease;
  text-decoration: underline; text-decoration-style: dotted;
  text-decoration-thickness: 1px; text-underline-offset: 4px;
  text-decoration-color: color-mix(in srgb, var(--accent) 55%, transparent);
}

/* Article */
.article {
  display: grid; grid-template-columns: 26px 1fr;
  gap: 2px 12px; padding: var(--row-pad) 0 calc(var(--row-pad) + 1px);
  border-bottom: 1px solid var(--rule-soft);
}
.article:last-child { border-bottom: none; }
.article .num {
  font-family: var(--font-meta); font-size: 11px;
  color: var(--faint); font-feature-settings: "tnum";
  padding-top: 4px; text-align: right;
}
.article .body { min-width: 0; }
.article h4 {
  margin: 0 0 4px; font-family: var(--font-body);
  font-weight: 500; font-size: 20px; line-height: 1.25;
  letter-spacing: -0.008em; text-wrap: pretty;
}
[data-font="sans"] .article h4 { font-weight: 600; letter-spacing: -0.015em; }
.article h4 a {
  background-image: linear-gradient(var(--ink), var(--ink));
  background-repeat: no-repeat; background-position: 0 100%;
  background-size: 0 1px;
  transition: background-size 180ms ease, color 120ms ease;
}
.article h4 a:hover {
  color: var(--hover);
  background-image: linear-gradient(var(--hover), var(--hover));
  background-size: 100% 1px;
}

/* Meta line — picks up the section accent */
.meta {
  font-family: var(--font-meta); font-size: 10px;
  letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--accent);
  display: flex; flex-wrap: wrap; align-items: center;
  gap: 0 6px; line-height: 1.4;
}
.meta .sep { color: var(--accent); opacity: 0.55; }
.meta a { color: #ed702e; border-bottom: 1px solid transparent; padding-bottom: 1px; }
.meta a:hover { color: #ff8a3c; border-color: currentColor; }
.meta .src { color: var(--accent); font-weight: 600; }
.meta .cat { color: var(--accent); }
.meta .reason {
  width: 100%; color: var(--faint); font-style: italic;
  text-transform: none; letter-spacing: 0;
  font-size: 11px; font-family: var(--font-body); margin-top: 1px;
}

/* Article collapsibles */
.article details { margin-top: 5px; }
.article details summary {
  list-style: none; cursor: pointer;
  font-family: var(--font-meta); font-size: 10px;
  letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--accent);
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 0; transition: color 120ms ease; user-select: none;
  text-decoration: underline; text-decoration-style: dotted;
  text-decoration-thickness: 1px; text-underline-offset: 3px;
  text-decoration-color: currentColor;
}
.article details summary::-webkit-details-marker { display: none; }
.article details summary:hover { color: var(--hover); }
.article details summary::before {
  content: "+"; display: inline-block; width: 9px;
  text-align: center; color: var(--faint);
  font-family: var(--font-meta);
  transition: transform 150ms ease, color 120ms ease;
}
.article details[open] summary::before { content: "−"; color: var(--accent); }
.article details[open] summary { color: var(--accent); }
.article details .panel {
  font-family: var(--font-body); font-size: 14px;
  font-weight: 500; line-height: 1.25;
  color: var(--panel-ink, var(--ink-soft));
  margin: 5px 0 4px; text-wrap: pretty;
}
[data-theme="dark"] { --panel-ink: #b5b5b5; }
.article details.abstract .panel { font-style: italic; color: var(--mute); }
details.section#nyt .article details.abstract .panel { font-style: normal; }

/* Side-by-side details (Abstract + Overview on NYT) */
.details-row { display: flex; flex-wrap: wrap; gap: 0 16px; margin-top: 4px; }
.details-row details { margin-top: 0; }

/* "More" chunk toggles (HN list): indent ONLY the button so expanded content is full-width */
.more { margin: 6px 0 0 0; }
.more > summary { display: inline-block; margin-left: 32px; padding: 4px 10px; border: 1px solid var(--rule); border-radius: 2px; color: var(--mute); cursor: pointer; font-size: 12px; letter-spacing: 0.04em; }
.more > summary:hover { color: var(--ink); border-color: var(--ink-soft); }
.more[open] > summary { margin-bottom: 6px; }

/* Toolbar icon glyphs */
.toolbar .ico { display: inline-block; min-width: 1em; text-align: center; }
.toolbar .ico-serif { font-family: 'Lora', Georgia, serif; font-weight: 600; }
.toolbar .ico-sans { font-family: 'Inter', -apple-system, sans-serif; font-weight: 600; }

/* Small screens: icons-only toolbar, fit one line */
@media (max-width: 560px) {
  .page { padding: 16px 16px 40px; }
  .masthead { padding: 20px 0 16px; margin-bottom: 22px; }
  .article { grid-template-columns: 22px 1fr; gap: 2px 10px; }
  .article h4 { font-size: 18px; }
  .toolbar { font-size: 11px; flex-wrap: nowrap; gap: 6px; align-items: center; }
  .toolbar .lbl { display: none; }
  .toolbar .archive { padding: 2px 4px; }
  .toolbar .btn { padding: 4px 6px; font-size: 12px; }
  .toolbar .seg .btn { padding: 4px 7px; }
  .controls { gap: 4px; flex-wrap: nowrap; }
  .section-head { flex-direction: column; align-items: flex-start; gap: 4px; }
}

a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 1px; }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""

_PAGE_SCRIPT = """(function () {
  var root = document.documentElement;
  function pressGroup(name, value) {
    document.querySelectorAll('[data-tg="' + name + '"]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', btn.dataset.val === value ? 'true' : 'false');
    });
  }
  function applyFont(v) {
    root.setAttribute('data-font', v);
    pressGroup('font', v);
    try { localStorage.setItem('dd-font', v); } catch (e) {}
  }
  function applyTheme(v) {
    root.setAttribute('data-theme', v);
    pressGroup('theme', v);
    try { localStorage.setItem('dd-theme', v); } catch (e) {}
  }
  document.querySelectorAll('[data-tg="font"]').forEach(function (btn) {
    btn.addEventListener('click', function () { applyFont(btn.dataset.val); });
  });
  document.querySelectorAll('[data-tg="theme"]').forEach(function (btn) {
    btn.addEventListener('click', function () { applyTheme(btn.dataset.val); });
  });
  var storedFont = null, storedTheme = null;
  try { storedFont = localStorage.getItem('dd-font'); storedTheme = localStorage.getItem('dd-theme'); } catch (e) {}
  applyFont(storedFont || 'serif');
  var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(storedTheme || (prefersDark ? 'dark' : 'light'));
  document.getElementById('expandAll').addEventListener('click', function () {
    document.querySelectorAll('details').forEach(function (d) { d.open = true; });
  });
  document.getElementById('collapseAll').addEventListener('click', function () {
    document.querySelectorAll('details').forEach(function (d) { d.open = false; });
  });
})();
"""


def _short_date_label(article):
    """Compact date for the meta line: 'Sun, May 24' (NYT) or 'Published: Sun, May 24' (HN/blogs/general)."""
    pub = article.get("date")
    day = None
    if isinstance(pub, datetime.date):
        day = pub
    elif pub:
        try:
            day = datetime.date.fromisoformat(str(pub)[:10])
        except ValueError:
            return str(pub)
    if not day:
        return ""
    if article.get("outlet") == "NYT":
        return day.strftime("%a, %b %-d")
    return day.strftime("%a, %b %-d")


def _render_meta(article, kind="generic"):
    """Per-kind meta line.
      HN  : HN \u00b7 date \u00b7 words/min \u00b7 pts \u00b7 comments \u00b7 HN Companion
      NYT : NYT \u00b7 section \u00b7 date \u00b7 words/min \u00b7 score
      blog: source \u00b7 category \u00b7 date \u00b7 words/min \u00b7 score
    """
    def span(text):
        return f'<span>{_html_escape(text)}</span>'

    def wc_span():
        wc, mins = article.get("word_count"), article.get("reading_time_minutes")
        if wc and mins:
            try:
                return f'<span>{int(wc):,} words \u00b7 {int(mins)} min read</span>'
            except (TypeError, ValueError):
                return None
        if mins:
            try:
                return f'<span>{int(mins)} min read</span>'
            except (TypeError, ValueError):
                return None
        return None

    def int_span(val, suffix):
        if val in (None, ""):
            return None
        try:
            return f'<span>{int(val)} {suffix}</span>'
        except (TypeError, ValueError):
            return None

    def score_span():
        sc = article.get("score")
        if sc in (None, ""):
            return None
        try:
            return f'<span>{float(sc):.1f}</span>'
        except (TypeError, ValueError):
            return None

    date_label = _short_date_label(article)
    parts = []
    if kind == "hn":
        parts.append(f'<span class="src">{_html_escape(article.get("outlet") or article.get("source") or "HN")}</span>')
        hn_url = article.get("hn_companion_url")
        if hn_url:
            parts.append(f'<a href="{_html_escape(hn_url)}" target="_blank">HN Companion \u2197</a>')
        if date_label: parts.append(span(date_label))
        wc = wc_span()
        if wc: parts.append(wc)
        pts = int_span(article.get("score"), "pts")
        if pts: parts.append(pts)
        cm = int_span(article.get("comments"), "comments")
        if cm: parts.append(cm)
    elif kind == "nyt":
        parts.append(f'<span class="src">{_html_escape(article.get("outlet") or "NYT")}</span>')
        sect = article.get("topic_tag") or article.get("section")
        if sect: parts.append(f'<span class="cat">{_html_escape(sect)}</span>')
        if date_label: parts.append(span(date_label))
        wc = wc_span()
        if wc: parts.append(wc)
        sc = score_span()
        if sc: parts.append(sc)
    else:
        src = article.get("source") or article.get("outlet")
        if src: parts.append(f'<span class="src">{_html_escape(src)}</span>')
        cat = article.get("topic_tag") or article.get("section")
        if cat: parts.append(f'<span class="cat">{_html_escape(cat)}</span>')
        if date_label: parts.append(span(date_label))
        wc = wc_span()
        if wc: parts.append(wc)
        sc = score_span()
        if sc: parts.append(sc)
    inner = '<span class="sep">\u00b7</span>'.join(parts)
    return f'<div class="meta">{inner}</div>'


def _render_collapsible(label, text, css_class=""):
    if not text:
        return ""
    cls_attr = f' class="{css_class}"' if css_class else ""
    return (
        f'<details{cls_attr}><summary>{_html_escape(label)}</summary>'
        f'<div class="panel">{_html_escape(text)}</div></details>'
    )


def _render_article(article, index, kind):
    """kind ∈ {'hn', 'nyt', 'blog', 'linkedin'} — controls meta + details layout."""
    num = f"{index:02d}"
    title = _html_escape(article.get("title", ""))
    url = _html_escape(article.get("url") or "#")
    meta = _render_meta(article, kind=kind)
    model = article.get("overview_model")
    ov_label = f"Overview (Model: {model})" if model else "Overview"
    if kind == "nyt":
        abstract_html = _render_collapsible("Abstract", article.get("abstract"), css_class="abstract")
        overview_html = _render_collapsible(ov_label, article.get("article_overview"))
        details_block = (
            f'<div class="details-row">{abstract_html}{overview_html}</div>'
            if (abstract_html or overview_html) else ""
        )
    else:
        overview_text = article.get("discussion_overview") or article.get("article_overview")
        # HN discussion overviews carry no model; only article_overview from blogs does.
        label = "Overview" if article.get("discussion_overview") else ov_label
        details_block = _render_collapsible(label, overview_text)
    return (
        '<article class="article">\n'
        f'  <div class="num">{num}</div>\n'
        '  <div class="body">\n'
        f'    <h4><a href="{url}" target="_blank">{title}</a></h4>\n'
        f'    {meta}\n'
        f'    {details_block}\n'
        '  </div>\n'
        '</article>'
    )


def _render_section(section_id, title, articles, kind):
    if not articles:
        return ""
    if kind == "hn":
        visible = articles[:5]
        second = articles[5:10]
        third = articles[10:16]
        first = "\n".join(_render_article(a, i, kind) for i, a in enumerate(visible, 1))
        items = first
        if second or third:
            items_second = "\n".join(_render_article(a, i, kind) for i, a in enumerate(second, 6))
            if third:
                items_third = "\n".join(_render_article(a, i, kind) for i, a in enumerate(third, 11))
                nested = f'<details class="more"><summary>More</summary>\n{items_third}\n</details>'
                inner = f'{items_second}\n{nested}' if items_second else nested
            else:
                inner = items_second
            items += f'\n<details class="more"><summary>More</summary>\n{inner}\n</details>'
    else:
        items = "\n".join(_render_article(a, i, kind) for i, a in enumerate(articles, 1))
    return (
        f'<details class="section" id="{section_id}" open>\n'
        f'  <summary class="section-head"><h2>{_html_escape(title)}</h2></summary>\n'
        f'{items}\n'
        '</details>'
    )


def _group_articles(articles, key):
    groups = []
    seen = {}
    for article in articles:
        label = article.get(key) or article.get("section") or article.get("source") or "Other"
        if label not in seen:
            seen[label] = []
            groups.append((label, seen[label]))
        seen[label].append(article)
    return groups


def _render_nyt_section(title, articles, group_key="topic_tag", note=""):
    if not articles:
        if not note:
            return ""
        body = (
            f'<p class="empty-note" style="margin:8px 0 0;color:var(--mute);'
            f'font-size:14px;line-height:1.5">{_html_escape(note)}</p>'
        )
        return (
            '<details class="section" id="nyt" open>\n'
            f'  <summary class="section-head"><h2>{_html_escape(title)}</h2></summary>\n'
            f'{body}\n'
            '</details>'
        )
    sub_blocks = []
    for label, group in _group_articles(articles, group_key):
        items = "\n".join(_render_article(a, i, "nyt") for i, a in enumerate(group, 1))
        sub_blocks.append(
            '<details class="subsection" open>\n'
            f'  <summary class="subhead"><h3>{_html_escape(label)}</h3></summary>\n'
            f'{items}\n'
            '</details>'
        )
    return (
        '<details class="section" id="nyt" open>\n'
        f'  <summary class="section-head"><h2>{_html_escape(title)}</h2></summary>\n'
        + "\n".join(sub_blocks) +
        '\n</details>'
    )




def digest_archive_entries(daily_html_dir=DAILY_HTML_DIR, href_prefix="output/daily_html"):
    entries = []
    for path in Path(daily_html_dir).glob("digest_*.html"):
        date_text = path.stem.removeprefix("digest_")
        try:
            digest_date = datetime.date.fromisoformat(date_text)
        except ValueError:
            continue
        label = f"{date_text} ({digest_date.strftime('%A')})"
        # If the digest's <title> carries a status suffix (e.g. "— NYT Archive
        # pending"), surface it on the archive page so it's clear at a glance
        # which days need to be re-run later.
        try:
            head = path.read_text(errors="ignore")[:2000]
            m = re.search(r"<title>\s*Daily Digest\s*[—-]\s*[^<]*?(—[^<]+)</title>", head)
            if m:
                label += f" {m.group(1).strip()}"
        except Exception:
            pass
        entries.append((date_text, label, f"{href_prefix.rstrip('/')}/{path.name}"))
    return sorted(entries, key=lambda entry: entry[0], reverse=True)


def generate_digest_archive_html(entries=None):
    entries = entries if entries is not None else digest_archive_entries()
    links = "\n".join(
        f'''      <li style="margin:0 0 10px"><a href="{_html_escape(href)}" style="color:#1a1a2e;text-decoration:none;font-weight:600">{_html_escape(label)}</a></li>'''
        for _, label, href in entries
    )
    if not links:
        links = '''      <li style="margin:0 0 10px;color:#777">No saved digests yet.</li>'''
    generated = datetime.datetime.now().strftime("%-I:%M %p on %B %-d, %Y")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Daily Digest Archive</title>
</head>
<body style="margin:0;padding:0;background:#f4f3f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#1a1a1a">
<table width="100%" cellspacing="0" cellpadding="0" style="background:#f4f3f0">
<tr><td align="center" style="padding:24px 16px">
<table width="660" cellspacing="0" cellpadding="0" style="max-width:660px;width:100%">
<tr><td style="background:#1a1a2e;border-radius:12px;padding:32px 36px">
  <div style="font-size:11px;color:rgba(255,255,255,.55);letter-spacing:.15em;text-transform:uppercase;margin-bottom:6px">Daily Digest</div>
  <div style="font-size:30px;font-weight:800;color:#fff;line-height:1.1">Past Daily Digests</div>
  <div style="font-size:14px;color:rgba(255,255,255,.5);margin-top:10px"><a href="index.html" style="color:rgba(255,255,255,.75);text-decoration:none">Back to latest digest</a></div>
</td></tr>
<tr><td style="height:16px"></td></tr>
<tr><td style="background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <ul style="list-style:none;margin:0;padding:0">
{links}
  </ul>
</td></tr>
<tr><td style="height:16px"></td></tr>
<tr><td style="text-align:center;padding:16px;font-size:11px;color:#999">
  Generated {_html_escape(generated)} · Daily Digest
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def write_digest_archive_page(path=DIGEST_ARCHIVE_PAGE):
    with open(path, "w", encoding="utf-8") as f:
        f.write(generate_digest_archive_html())


def generate_html(date, data, settings=None, archive_href="digest_archive.html"):
    settings = settings or DEFAULT_CONFIG["settings"]
    sections = build_sections(data, settings)
    date_str = digest_date_for(date).strftime("%A, %B %-d, %Y")
    if data.get("nyt_note"):
        date_str = f"{date_str} — NYT Archive pending"
    fetched_str = datetime.datetime.now().strftime("%A, %b %d, %Y at %H:%M")

    body_parts = [
        _render_section("hn", "Yesterday on Hacker News", sections["hn"], "hn"),
        _render_nyt_section("Today on The New York Times", sections["nyt_wsj"], note=data.get("nyt_note", "")),
        _render_section("research", "MIT & Sloan Research", sections["research"], "blog"),
        _render_section("blogs", "Blog Posts", sections["blogs"], "blog"),
        _render_section("linkedin", "From the Network", sections["linkedin"], "linkedin"),
    ]
    body_sections = "\n\n".join(part for part in body_parts if part)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Digest — {_html_escape(date_str)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400..700;1,400..700&family=Inter:wght@400;500;600&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">
<style>
{_PAGE_CSS}
</style>
</head>
<body>
<div class="page">
  <div class="toolbar">
    <a class="archive" href="{_html_escape(archive_href)}" title="Read past daily digests"><span class="ico" aria-hidden="true">←</span><span class="lbl"> Read past daily digests</span></a>
    <div class="controls">
      <div class="seg" role="group" aria-label="Font">
        <button class="btn" data-tg="font" data-val="serif" aria-pressed="true" title="Serif"><span class="ico ico-serif" aria-hidden="true">A</span><span class="lbl"> Serif</span></button>
        <button class="btn" data-tg="font" data-val="sans" aria-pressed="false" title="Sans"><span class="ico ico-sans" aria-hidden="true">A</span><span class="lbl"> Sans</span></button>
      </div>
      <div class="seg" role="group" aria-label="Theme">
        <button class="btn" data-tg="theme" data-val="light" aria-pressed="true" title="Light"><span class="ico" aria-hidden="true">☀</span><span class="lbl"> Light</span></button>
        <button class="btn" data-tg="theme" data-val="dark" aria-pressed="false" title="Dark"><span class="ico" aria-hidden="true">☾</span><span class="lbl"> Dark</span></button>
      </div>
      <button class="btn" id="expandAll" title="Expand all"><span class="ico" aria-hidden="true">▾</span><span class="lbl"> Expand all</span></button>
      <button class="btn" id="collapseAll" title="Collapse all"><span class="ico" aria-hidden="true">▴</span><span class="lbl"> Collapse all</span></button>
    </div>
  </div>

  <header class="masthead">
    <div class="kicker">Your Morning Read</div>
    <h1 class="wordmark">{_html_escape(date_str)}</h1>
    <div class="fetched">Fetched: {_html_escape(fetched_str)}</div>
  </header>

{body_sections}

</div>
<script>
{_PAGE_SCRIPT}
</script>
</body>
</html>
"""

def _md_articles(articles, numbered=False, show_score=False):
    lines = []
    for index, article in enumerate(articles, 1):
        title = _md_escape(article.get("title", ""))
        url = article.get("url", "#")
        source = article.get("outlet") or article.get("source", "")
        section = article.get("topic_tag") or article.get("section", "")
        badge = f"**[{_md_escape(source)}" + (f" · {_md_escape(section)}" if section else "") + "]** " if source else ""
        score = ""
        if show_score and article.get("comments") is not None:
            score = f" {int(article.get('score', 0))} pts · {int(article.get('comments', 0))} comments"
        elif article.get("score") not in (None, ""):
            score = f" · score {float(article.get('score', 0)):.1f}"
        reading_meta = _reading_meta_text(article)
        if reading_meta:
            score += f" · {_md_escape(reading_meta)}"
        discussion_url = article.get("hn_companion_url") or article.get("hn_url")
        discuss_label = "HN Companion" if article.get("hn_companion_url") else "discuss"
        discuss = f" · [{discuss_label}]({discussion_url})" if discussion_url else ""
        date_note = f" · {_md_escape(_display_date(article))}" if _display_date(article) else ""
        prefix = f"{index}." if numbered else "-"
        lines.append(f"{prefix} {badge}[{title}]({url}){score}{discuss}{date_note}")
        if article.get("abstract"):
            lines.append(f"   - **Abstract:** {_md_escape(article['abstract'])}")
        overview_text = article.get("discussion_overview") or article.get("article_overview")
        if overview_text:
            model_name = article.get("overview_model") or ""
            overview_label = f"Overview (Model: {model_name}):" if model_name else "Overview:"
            lines.append(f"   - **{overview_label}** {_md_escape(overview_text)}")
        if article.get("reason") or article.get("reading_mode"):
            detail = " — ".join(part for part in (article.get("reading_mode"), article.get("reason")) if part)
            lines.append(f"   - {_md_escape(detail)}")
    return "\n".join(lines)


def _md_grouped_articles(articles, group_key):
    lines = []
    for label, group in _group_articles(articles, group_key):
        lines.extend([f"#### {_md_escape(label)}", ""])
        lines.append(_md_articles(group, numbered=True))
        lines.append("")
    return "\n".join(lines).rstrip()


def generate_markdown(date, data, settings=None):
    settings = settings or DEFAULT_CONFIG["settings"]
    sections = build_sections(data, settings)
    date_str = digest_date_for(date).strftime("%A, %B %-d, %Y")
    if data.get("nyt_note"):
        date_str = f"{date_str} — NYT Archive pending"
    fetched_str = datetime.datetime.now().strftime("%A, %b %d, %Y at %H:%M")

    parts = [f"# Daily Digest - {date_str}", "", f"*Fetched: {fetched_str}*", "", "---", ""]

    def sec(heading, articles, numbered=False, score=False):
        if not articles:
            return []
        return [f"### {heading}", "", _md_articles(articles, numbered=numbered, show_score=score), ""]

    def grouped_sec(heading, articles, group_key):
        if not articles:
            return []
        return [f"### {heading}", "", _md_grouped_articles(articles, group_key), ""]

    parts += sec("🔶 Yesterday's Top HackerNews", sections["hn"], numbered=True, score=True)
    nyt_note = data.get("nyt_note", "")
    if sections["nyt_wsj"]:
        parts += grouped_sec("📰 Today on The New York Times", sections["nyt_wsj"], "topic_tag")
    elif nyt_note:
        parts += ["### 📰 Today on The New York Times", "", f"*{nyt_note}*", ""]
    parts += sec("🎓 MIT & Sloan Research", sections["research"], numbered=True)
    parts += sec("📚 Blog Posts", sections["blogs"], numbered=True)
    parts += sec("💼 LinkedIn - Rama's Activity", sections["linkedin"])
    parts += ["---", f"*Generated {datetime.datetime.now().strftime('%-I:%M %p')} on {datetime.date.today().strftime('%B %-d, %Y')}*"]
    return "\n".join(parts)


def _hn_md_table(archive):
    lines = [
        "# HackerNews Daily Top 10 - Archive",
        "",
        f"*Last updated: {datetime.date.today().isoformat()} · {len(archive)} days · {sum(len(v) for v in archive.values())} stories*",
        "",
        "| Date | Day | Rank | Title | Points | Comments | Topic | URL |",
        "|------|-----|------|-------|--------|----------|-------|-----|",
    ]
    for date_str in sorted(archive.keys(), reverse=True):
        day = datetime.date.fromisoformat(date_str).strftime("%A")
        for rank, story in enumerate(archive[date_str][:10], 1):
            title = _table_escape(story.get("title", ""))
            url = story.get("url", "")
            hn_url = story.get("hn_url", "")
            link = f"[link]({url})" if url else ""
            hn_link = f"[HN]({hn_url})" if hn_url else ""
            links = " · ".join(part for part in (link, hn_link) if part)
            lines.append(
                f"| {date_str} | {day} | {rank} | {title} | {int(story.get('score', 0))} | "
                f"{int(story.get('comments', 0))} | Technology | {links} |"
            )
    return "\n".join(lines)


def hn_archive_rows(archive):
    rows = []
    for date_str in sorted(archive.keys(), reverse=True):
        day = datetime.date.fromisoformat(date_str).strftime("%A")
        for rank, story in enumerate(archive[date_str][:10], 1):
            url = story.get("url", "")
            hn_url = story.get("hn_url", "")
            combined_url = " · ".join(part for part in (url, hn_url) if part)
            rows.append([
                date_str,
                day,
                rank,
                story.get("title", ""),
                int(story.get("score", 0)),
                int(story.get("comments", 0)),
                "Technology",
                combined_url,
            ])
    return rows


def write_hn_archive_xlsx(archive, path):
    write_xlsx(
        path,
        "HN Archive",
        ["Date", "Day", "Rank", "Title", "Points", "Comments", "Topic", "URL"],
        hn_archive_rows(archive),
    )


def update_hn_archive(date, new_stories):
    if not new_stories:
        print("  [HN Archive] No stories - skipping.")
        return

    HN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_path = HN_DATA_DIR / "hn_archive_data.json"
    md_path = HN_DATA_DIR / "hn_archive.md"
    xlsx_path = HN_DATA_DIR / "hn_archive.xlsx"
    archive = {}
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as f:
                archive = json.load(f)
        except Exception as e:
            print(f"  [HN Archive] Error reading JSON: {e}; rebuilding from current run.")
            archive = {}

    date_key = date.isoformat()
    if date_key not in archive:
        archive[date_key] = new_stories[:10]
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(archive, f, ensure_ascii=False, indent=2, default=str)
            print(f"  [HN Archive] Added {len(archive[date_key])} stories for {date_key}")
        except Exception as e:
            print(f"  [HN Archive] Error writing JSON: {e}")
    else:
        print(f"  [HN Archive] {date_key} already present - skipping JSON update")

    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_hn_md_table(archive))
        print("  [HN Archive] Regenerated hn_archive.md")
    except Exception as e:
        print(f"  [HN Archive] Error writing markdown: {e}")

    try:
        write_hn_archive_xlsx(archive, xlsx_path)
        print("  [HN Archive] Regenerated hn_archive.xlsx")
    except Exception as e:
        print(f"  [HN Archive] Error writing xlsx: {e}")


def _flatten_digest(date, data, settings=None):
    records = []
    digest_date = date.isoformat()
    seen = set()
    sections = build_sections(data, settings or DEFAULT_CONFIG["settings"])

    def add(articles):
        for article in articles:
            key = article_key(article)
            if not article.get("title") or not article.get("url") or key in seen:
                continue
            seen.add(key)
            pub = article.get("date")
            pub_str = pub.isoformat() if isinstance(pub, datetime.date) else (pub or "")
            records.append({
                "digest_date": digest_date,
                "title": article.get("title", "").strip(),
                "source": (article.get("outlet") or article.get("source", "")).strip(),
                "section": (article.get("section") or "").strip(),
                "topic": _infer_topic(article),
                "category": article.get("category", ""),
                "pub_date": pub_str,
                "url": article.get("url", ""),
                "hn_url": article.get("hn_url", ""),
            })

    for key in (
        "hn", "nyt_wsj", "research", "blogs", "linkedin"
    ):
        add(sections.get(key, []))
    return records


def _dd_md_table(archive):
    total = sum(len(items) for items in archive.values())
    lines = [
        "# Daily Digest - Archive",
        "",
        f"*Last updated: {datetime.date.today().isoformat()} · {total} items*",
        "",
        "| Digest Date | Title | Source | Topic | Category | Pub Date | URL |",
        "|-------------|-------|--------|-------|----------|----------|-----|",
    ]
    for date_str in sorted(archive.keys(), reverse=True):
        for record in archive[date_str]:
            title = _table_escape(record.get("title", ""))[:100]
            source = _table_escape(record.get("source", ""))
            if record.get("section"):
                source = f"{source} · {_table_escape(record['section'])}"
            url = record.get("url", "")
            link = f"[link]({url})" if url else ""
            lines.append(
                f"| {record.get('digest_date', date_str)} | {title} | {source} | "
                f"{_table_escape(record.get('topic', ''))} | {_table_escape(record.get('category', ''))} | "
                f"{_table_escape(record.get('pub_date', ''))} | {link} |"
            )
    return "\n".join(lines)


def dd_archive_rows(archive):
    rows = []
    for date_str in sorted(archive.keys(), reverse=True):
        for record in archive[date_str]:
            source = record.get("source", "")
            if record.get("section"):
                source = f"{source} · {record['section']}"
            rows.append([
                record.get("digest_date", date_str),
                record.get("title", ""),
                source,
                record.get("topic", ""),
                record.get("category", ""),
                record.get("pub_date", ""),
                record.get("url", ""),
            ])
    return rows


def write_dd_archive_xlsx(archive, path):
    write_xlsx(
        path,
        "Digest Archive",
        ["Digest Date", "Title", "Source", "Topic", "Category", "Pub Date", "URL"],
        dd_archive_rows(archive),
    )


def update_dd_archive(date, data, settings=None):
    DIGEST_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    json_path = DIGEST_ARCHIVE_DIR / "dd_archive_data.json"
    md_path = DIGEST_ARCHIVE_DIR / "dd_archive.md"
    xlsx_path = DIGEST_ARCHIVE_DIR / "dd_archive.xlsx"
    archive = {}
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as f:
                archive = json.load(f)
        except Exception as e:
            print(f"  [DD Archive] Error reading JSON: {e}; rebuilding from current run.")
            archive = {}

    date_key = date.isoformat()
    records = _flatten_digest(date, data, settings=settings)
    existing = archive.get(date_key, [])
    if existing and len(records) < max(5, len(existing) // 2):
        print(
            f"  [DD Archive] Kept existing {len(existing)} items for {date_key}; "
            f"current run only found {len(records)} items."
        )
    else:
        action = "Replaced" if existing else "Added"
        archive[date_key] = records
        print(f"  [DD Archive] {action} {len(records)} items for {date_key}")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"  [DD Archive] Error writing JSON: {e}")

    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_dd_md_table(archive))
        print("  [DD Archive] Regenerated dd_archive.md")
    except Exception as e:
        print(f"  [DD Archive] Error writing markdown: {e}")

    try:
        write_dd_archive_xlsx(archive, xlsx_path)
        print("  [DD Archive] Regenerated dd_archive.xlsx")
    except Exception as e:
        print(f"  [DD Archive] Error writing xlsx: {e}")


def _clear_stale_git_lock(max_age_seconds=300):
    lock_path = REPO_ROOT / ".git" / "index.lock"
    if not lock_path.exists():
        return False
    try:
        age = time.time() - lock_path.stat().st_mtime
        if age < max_age_seconds:
            print(f"  [GitHub] Git index lock is recent ({int(age)}s old); not removing it.")
            return False
        lock_path.unlink()
        print(f"  [GitHub] Removed stale git index lock: {lock_path}")
        return True
    except Exception as e:
        print(f"  [GitHub] Could not remove stale git index lock: {e}")
        return False


def _run_git(args):
    if args and args[0] in {"add", "commit"}:
        _clear_stale_git_lock()
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT)] + args,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown git error").strip()
        if "index.lock" in message and _clear_stale_git_lock(max_age_seconds=0):
            result = subprocess.run(
                ["git", "-C", str(REPO_ROOT)] + args,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode == 0:
                return True, result
            message = (result.stderr or result.stdout or "unknown git error").strip()
        print(f"  [GitHub] git {' '.join(args)} failed: {message}")
        return False, result
    return True, result


def _has_publishable_content(data):
    return any(data.get(key) for key in ("hn", "nyt_wsj", "blogs"))


def _run_nyt_wsj_ranker(date, settings):
    """Return (selected_articles, optional_note). The note flags lag cases so the
    renderer can show a placeholder instead of a silently-empty NYT section."""
    if not HAS_NYT_WSJ_RANKER or run_nyt_wsj_ranker is None:
        print("  [NYT/WSJ Ranker] Error: nyt_wsj_rss_ranker.py could not be imported.")
        return [], ""
    try:
        result = run_nyt_wsj_ranker(
            target_date=date,
            max_links=int(settings.get("nyt_wsj_max_links", 20)),
            output_dir=REPO_ROOT / settings.get("ranker_output_dir", "output/ranker_diagnostics"),
            write_files=True,
        )
        selected = result.get("selected", [])
        stats = result.get("stats", {}) or {}
        note = ""
        if not selected and stats.get("source") == "archive":
            news_date = date + datetime.timedelta(days=1)
            note = (
                f"The NYT Archive API has not yet indexed articles for {news_date.isoformat()}. "
                "NYT typically lags ~1–2 days in production; this snapshot is lagging further. "
                "Re-run this date later to populate this section."
            )
        return selected, note
    except Exception as e:
        print(f"  [NYT/WSJ Ranker] Error: {e}")
        return [], ""


def _run_blog_ranker(date, settings):
    if not HAS_BLOG_RANKER or run_blog_ranker is None:
        print("  [Blog Ranker] Error: blog_reading_ranker.py could not be imported.")
        return []
    try:
        result = run_blog_ranker(
            target_date=date,
            max_links=int(settings.get("blog_max_links", 20)),
            output_dir=REPO_ROOT / settings.get("ranker_output_dir", "output/ranker_diagnostics"),
            write_files=True,
        )
        return result.get("selected", [])
    except Exception as e:
        print(f"  [Blog Ranker] Error: {e}")
        return []


def github_pages_publish_files(date, settings, daily_html_dir=DAILY_HTML_DIR):
    ddate = digest_date_for(date)
    files = [
        str(Path("output") / "daily_html" / f"digest_{ddate.isoformat()}.html"),
        str(Path("output") / "daily_md" / f"digest_{ddate.isoformat()}.md"),
        "index.html",
        "digest_archive.html",
        str(Path("data") / "hn" / "hn_archive.md"),
        str(Path("data") / "hn" / "hn_archive.xlsx"),
        str(Path("data") / "digest_archives" / "dd_archive.md"),
        str(Path("data") / "digest_archives" / "dd_archive.xlsx"),
    ]
    for _, _, href in digest_archive_entries(daily_html_dir):
        files.append(href)

    output_dir = REPO_ROOT / settings.get("ranker_output_dir", "output/ranker_diagnostics")
    for name in (
        f"nyt_wsj_briefing_{date.isoformat()}.md",
        f"nyt_wsj_candidates_{date.isoformat()}.csv",
        f"blog_briefing_{date.isoformat()}.md",
        f"blog_candidates_{date.isoformat()}.csv",
    ):
        if (output_dir / name).exists():
            files.append(str(Path(settings.get("ranker_output_dir", "output/ranker_diagnostics")) / name))

    return list(dict.fromkeys(files))


def push_to_github(date, config):
    if os.environ.get("DAILY_DIGEST_SKIP_GITHUB") == "1":
        print("  [GitHub] Skipped because DAILY_DIGEST_SKIP_GITHUB=1.")
        return
    if not config.get("github_pages", {}).get("enabled"):
        return
    settings = config.get("settings", {})
    try:
        files = github_pages_publish_files(date, settings)
        ok, _ = _run_git(["add"] + files)
        if not ok:
            return
        ok, result = _run_git(["commit", "-m", f"digest: {date.isoformat()}"])
        if not ok:
            text = f"{result.stdout}\n{result.stderr}".lower()
            if "nothing to commit" in text:
                print("  [GitHub] Nothing new to commit.")
            return
        ok, _ = _run_git(["push", "origin", "main"])
        if ok:
            print("  [GitHub] Pushed to GitHub Pages")
    except Exception as e:
        print(f"  [GitHub] Error: {e}")


def main(target_date=None, summary_config=None):
    print(f"\n{'-' * 50}")
    print(f"  Daily Digest - {datetime.date.today()}")
    print(f"{'-' * 50}")

    config = load_config()
    settings = config.get("settings", {})
    date = target_date or get_yesterday()
    print(f"  Fetching content for: {date}\n")

    print("  [1/7] HackerNews...")
    hn = fetch_hackernews(n=int(settings.get("hn_digest_count", 16)), date=date, verbose=True)

    print("  [2/7] NYT ranker...")
    nyt_wsj, nyt_note = _run_nyt_wsj_ranker(date, settings)

    print("  [3/7] Blog / research ranker...")
    blogs = _run_blog_ranker(date, settings)

    data = {
        "hn": hn,
        "nyt_wsj": nyt_wsj,
        "blogs": blogs,
        "linkedin": [],
        "nyt_note": nyt_note,
    }

    print("  [4/7] Reading stats...")
    enrich_article_reading_stats(data)

    print("  [5/7] Article overviews...")
    summary_config = summary_config or {"provider": "none", "model": "", "nyt_sections": set()}
    enrich_article_overviews(data, summary_config, settings)
    fill_nyt_abstracts(data)

    print("  [6/7] LinkedIn...")
    data["linkedin"] = fetch_linkedin_activity()

    print("  [7/7] Rendering and archives...")
    publishable = _has_publishable_content(data)

    ddate = digest_date_for(date)
    html_path = DAILY_HTML_DIR / f"digest_{ddate.isoformat()}.html"
    md_path = DAILY_MD_DIR / f"digest_{ddate.isoformat()}.md"
    index_path = REPO_ROOT / "index.html"
    preserve_existing_outputs = (
        not publishable
        and (html_path.exists() or md_path.exists() or index_path.exists())
    )

    print("\n  Generating outputs...")
    html_doc = generate_html(date, data, settings, archive_href="../../digest_archive.html")
    index_html_doc = generate_html(date, data, settings, archive_href="digest_archive.html")
    markdown = generate_markdown(date, data, settings)

    if preserve_existing_outputs:
        print("  [Output] Skipped writing digest files because no network-fetched content was available.")
        print("  [Output] Preserved existing digest/index files.")
        try:
            if html_path.exists():
                html_doc = html_path.read_text(encoding="utf-8")
            elif index_path.exists():
                html_doc = index_path.read_text(encoding="utf-8")
            if index_path.exists():
                index_html_doc = index_path.read_text(encoding="utf-8")
            if md_path.exists():
                markdown = md_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [Output] Error reading preserved digest files: {e}")
    else:
        try:
            DAILY_HTML_DIR.mkdir(parents=True, exist_ok=True)
            DAILY_MD_DIR.mkdir(parents=True, exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_doc)
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(index_html_doc)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(markdown)
            print(f"  Saved HTML:     {html_path.relative_to(REPO_ROOT)}")
            print("  Saved index.html")
            print(f"  Saved Markdown: {md_path.relative_to(REPO_ROOT)}")
        except Exception as e:
            print(f"  [Output] Error writing digest files: {e}")

    try:
        write_digest_archive_page()
        print(f"  Saved archive:  {DIGEST_ARCHIVE_PAGE.relative_to(REPO_ROOT)}")
    except Exception as e:
        print(f"  [Output] Error writing digest archive page: {e}")

    if publishable:
        print("  Updating HN archive...")
        update_hn_archive(date, hn)

        print("  Updating digest archive...")
        update_dd_archive(date, data, settings=settings)
    else:
        print("  [Archive] Skipped archive updates because no network-fetched content was available.")

    print("  GitHub Pages...")
    if publishable:
        push_to_github(date, config)
    else:
        print("  [GitHub] Skipped publish because no network-fetched content was available.")

    print("\n  Done!\n")
    return {
        "html_path": str(html_path),
        "md_path": str(md_path),
        "html": html_doc,
        "md": markdown,
        "date": date.isoformat(),
    }


def cli(argv=None):
    args = parse_args(argv)
    target_date = content_date_from_user_input(args.date) if args.date else None
    try:
        summary_config = summary_config_from_args(args)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    if args.date:
        print(f"  User input date: {args.date}")
        print(f"  Content date:    {target_date.isoformat()} (previous day)")
    return main(target_date=target_date, summary_config=summary_config)


if __name__ == "__main__":
    cli()
