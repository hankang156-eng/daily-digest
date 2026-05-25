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
GEMINI_MAX_ATTEMPTS = 4
GEMINI_BACKOFF_BASE = 2.0
GEMINI_MIN_INTERVAL = 1.0

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


def _gemini_generate(prompt, model, api_key):
    last_error = None
    for attempt in range(GEMINI_MAX_ATTEMPTS):
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
            if attempt < GEMINI_MAX_ATTEMPTS - 1:
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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "", ""
    models = [model]
    if GEMINI_FALLBACK_MODEL not in models:
        models.append(GEMINI_FALLBACK_MODEL)
    last_error = None
    for index, candidate in enumerate(models):
        try:
            return _gemini_generate(prompt, candidate, api_key), candidate
        except Exception as e:
            last_error = e
            if index < len(models) - 1:
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
    if config.get("provider") == "none":
        print("  [Summaries] Disabled.")
        return data

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


def _badge(text, bg="#eeeeee", color="#333333"):
    return (
        f'<span style="display:inline-block;background:{bg};color:{color};font-size:10px;'
        f'font-weight:700;padding:2px 6px;border-radius:4px;letter-spacing:.04em;'
        f'margin-left:4px;vertical-align:middle">{_html_escape(text)}</span>'
    )


def _article_row(article, show_score=False):
    outlet_colors = {
        "NYT": ("#111111", "#ffffff"),
        "WSJ": ("#00285e", "#ffffff"),
        "HN": ("#ff6600", "#ffffff"),
    }
    badges = ""
    outlet = article.get("outlet")
    if outlet in outlet_colors:
        bg, fg = outlet_colors[outlet]
        badges += _badge(outlet, bg, fg)
    if article.get("section"):
        badges += _badge(article["section"], "#f0f0f0", "#666666")
    if article.get("source") and not outlet:
        badges += _badge(article["source"], "#f5f5f5", "#777777")

    score = ""
    if show_score and article.get("comments") is not None:
        score = (
            f' <span style="font-size:11px;color:#888;margin-left:6px">'
            f'{int(article.get("score", 0))} pts · {int(article.get("comments", 0))} comments</span>'
        )
    elif article.get("score") not in (None, ""):
        score = (
            f' <span style="font-size:11px;color:#888;margin-left:6px">'
            f'Score {float(article.get("score", 0)):.1f}</span>'
        )
    reading_meta = _reading_meta_text(article)
    if reading_meta:
        score += (
            f' <span style="font-size:11px;color:#888;margin-left:6px">'
            f'{_html_escape(reading_meta)}</span>'
        )
    discussion_url = article.get("hn_companion_url") or article.get("hn_url")
    discuss = ""
    if discussion_url:
        label = "HN Companion" if article.get("hn_companion_url") else "discuss"
        discuss = (
            f' <a href="{_html_escape(discussion_url)}" style="font-size:11px;color:#e67e22;'
            f'margin-left:6px;text-decoration:none">{label}</a>'
        )
    date_note = _display_date(article)
    if date_note:
        date_note = f' <span style="font-size:11px;color:#999;margin-left:6px">{_html_escape(date_note)}</span>'
    mode = article.get("reading_mode")
    reason = article.get("reason")
    detail = ""
    if mode or reason:
        detail = (
            f'<div style="font-size:12px;color:#777;line-height:1.35;margin-top:4px">'
            f'{_html_escape(mode or "")}'
            f'{": " if mode and reason else ""}'
            f'{_html_escape(reason or "")}</div>'
        )

    return f'''<tr>
      <td style="padding:9px 0;border-bottom:1px solid #f5f5f5;vertical-align:top">
        <a href="{_html_escape(article.get("url", "#"))}" style="color:#1a1a2e;font-size:14px;text-decoration:none;line-height:1.45;font-weight:500" target="_blank">{_html_escape(article.get("title", ""))}</a>
        {badges}{score}{discuss}{date_note}
        {detail}
      </td>
    </tr>'''


def _article_list_item(article, index=None, show_score=False):
    number = f'<span style="display:inline-block;width:24px;color:#999">{index}.</span>' if index is not None else ""
    return f'''<tr>
      <td style="padding:9px 0;border-bottom:1px solid #f5f5f5;vertical-align:top">
        {number}{_article_row_content(article, show_score=show_score)}
      </td>
    </tr>'''


def _article_row_content(article, show_score=False):
    outlet_colors = {
        "NYT": ("#111111", "#ffffff"),
        "WSJ": ("#00285e", "#ffffff"),
        "HN": ("#ff6600", "#ffffff"),
    }
    badges = ""
    outlet = article.get("outlet")
    if outlet in outlet_colors:
        bg, fg = outlet_colors[outlet]
        badges += _badge(outlet, bg, fg)
    tag = article.get("topic_tag") or article.get("section")
    if tag:
        badges += _badge(tag, "#f0f0f0", "#666666")
    if article.get("source") and not outlet:
        badges += _badge(article["source"], "#f5f5f5", "#777777")

    score = ""
    if show_score and article.get("comments") is not None:
        score = (
            f' <span style="font-size:11px;color:#888;margin-left:6px">'
            f'{int(article.get("score", 0))} pts · {int(article.get("comments", 0))} comments</span>'
        )
    elif article.get("score") not in (None, ""):
        score = (
            f' <span style="font-size:11px;color:#888;margin-left:6px">'
            f'Score {float(article.get("score", 0)):.1f}</span>'
        )
    reading_meta = _reading_meta_text(article)
    if reading_meta:
        score += (
            f' <span style="font-size:11px;color:#888;margin-left:6px">'
            f'{_html_escape(reading_meta)}</span>'
        )
    discussion_url = article.get("hn_companion_url") or article.get("hn_url")
    discuss = ""
    if discussion_url:
        label = "HN Companion" if article.get("hn_companion_url") else "discuss"
        discuss = (
            f' <a href="{_html_escape(discussion_url)}" style="font-size:11px;color:#e67e22;'
            f'margin-left:6px;text-decoration:none">{label}</a>'
        )
    date_note = _display_date(article)
    if date_note:
        date_note = f' <span style="font-size:11px;color:#999;margin-left:6px">{_html_escape(date_note)}</span>'
    mode = article.get("reading_mode")
    reason = article.get("reason")
    detail = ""
    if mode or reason:
        detail = (
            f'<div style="font-size:12px;color:#777;line-height:1.35;margin-top:4px;margin-left:24px">'
            f'{_html_escape(mode or "")}'
            f'{": " if mode and reason else ""}'
            f'{_html_escape(reason or "")}</div>'
        )
    abstract_text = article.get("abstract")
    abstract = ""
    if abstract_text:
        abstract = (
            f'<div style="font-size:12px;color:#555;line-height:1.45;margin-top:6px;margin-left:24px">'
            f'<strong>Abstract:</strong> {_html_escape(abstract_text)}</div>'
        )
    overview_text = article.get("discussion_overview") or article.get("article_overview")
    overview = ""
    if overview_text:
        model_name = article.get("overview_model") or ""
        overview_label = f"Overview (Model: {model_name}):" if model_name else "Overview:"
        overview = (
            f'<div style="font-size:12px;color:#555;line-height:1.45;margin-top:6px;margin-left:24px">'
            f'<strong>{_html_escape(overview_label)}</strong> {_html_escape(overview_text)}</div>'
        )
    return (
        f'<a href="{_html_escape(article.get("url", "#"))}" style="color:#1a1a2e;font-size:14px;'
        f'text-decoration:none;line-height:1.45;font-weight:500" target="_blank">'
        f'{_html_escape(article.get("title", ""))}</a>{badges}{score}{discuss}{date_note}{detail}{abstract}{overview}'
    )


def _section_block(heading, articles, show_score=False, numbered=False):
    if not articles:
        return ""
    rows = "".join(
        _article_list_item(article, index=index if numbered else None, show_score=show_score)
        for index, article in enumerate(articles, 1)
    )
    return f'''
    <div style="margin-bottom:28px">
      <h3 style="font-size:13px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px;padding-bottom:8px;border-bottom:2px solid #f0f0f0">{heading}</h3>
      <table width="100%" cellspacing="0" cellpadding="0"><tbody>{rows}</tbody></table>
    </div>'''


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


def _grouped_section_block(heading, articles, group_key, numbered=True):
    if not articles:
        return ""
    blocks = [f'''
    <div style="margin-bottom:28px">
      <h3 style="font-size:13px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px;padding-bottom:8px;border-bottom:2px solid #f0f0f0">{heading}</h3>''']
    for label, group in _group_articles(articles, group_key):
        rows = "".join(
            _article_list_item(article, index=index if numbered else None)
            for index, article in enumerate(group, 1)
        )
        blocks.append(f'''
      <div style="font-size:12px;font-weight:800;color:#1a1a2e;margin:18px 0 6px">{_html_escape(label)}</div>
      <table width="100%" cellspacing="0" cellpadding="0"><tbody>{rows}</tbody></table>''')
    blocks.append("</div>")
    return "".join(blocks)


def digest_archive_entries(daily_html_dir=DAILY_HTML_DIR, href_prefix="output/daily_html"):
    entries = []
    for path in Path(daily_html_dir).glob("digest_*.html"):
        date_text = path.stem.removeprefix("digest_")
        try:
            digest_date = datetime.date.fromisoformat(date_text)
        except ValueError:
            continue
        entries.append((
            date_text,
            f"{date_text} ({digest_date.strftime('%A')})",
            f"{href_prefix.rstrip('/')}/{path.name}",
        ))
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
    fetched_str = datetime.datetime.now().strftime("%A, %b %d, %Y at %H:%M")

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Daily Digest - {_html_escape(date_str)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f3f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#1a1a1a">
<table width="100%" cellspacing="0" cellpadding="0" style="background:#f4f3f0">
<tr><td align="center" style="padding:24px 16px">
<table width="660" cellspacing="0" cellpadding="0" style="max-width:660px;width:100%">
<tr><td style="background:#1a1a2e;border-radius:12px;padding:32px 36px">
  <div style="font-size:11px;color:rgba(255,255,255,.55);letter-spacing:.15em;text-transform:uppercase;margin-bottom:6px">Your Morning Read</div>
  <div style="font-size:30px;font-weight:800;color:#fff;line-height:1.1">{_html_escape(date_str)}</div>
  <div style="font-size:12px;color:rgba(255,255,255,.6);margin-top:6px">Fetched: {_html_escape(fetched_str)}</div>
  <div style="font-size:14px;color:rgba(255,255,255,.5);margin-top:6px">Daily Digest</div>
  <div style="font-size:13px;margin-top:18px"><a href="{_html_escape(archive_href)}" style="color:rgba(255,255,255,.82);text-decoration:none;font-weight:700">Read past daily digests</a></div>
</td></tr>
<tr><td style="height:16px"></td></tr>
<tr><td style="background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  {_section_block("🔶 Yesterday's Top HackerNews", sections["hn"], show_score=True, numbered=True)}
  {_grouped_section_block("📰 NYT Strategic Reading List", sections["nyt_wsj"], "topic_tag")}
  {_section_block("🎓 MIT & Sloan Research", sections["research"], numbered=True)}
  {_section_block("📚 Blogs & Craft", sections["blogs"], numbered=True)}
  {_section_block("💼 LinkedIn - Rama's Activity", sections["linkedin"])}
</td></tr>
<tr><td style="height:16px"></td></tr>
<tr><td style="text-align:center;padding:16px;font-size:11px;color:#999">
  Generated {_html_escape(datetime.datetime.now().strftime("%-I:%M %p on %B %-d, %Y"))} · Daily Digest
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
    return html_doc


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
    parts += grouped_sec("📰 NYT Strategic Reading List", sections["nyt_wsj"], "topic_tag")
    parts += sec("🎓 MIT & Sloan Research", sections["research"], numbered=True)
    parts += sec("📚 Blogs & Craft", sections["blogs"], numbered=True)
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
    if not HAS_NYT_WSJ_RANKER or run_nyt_wsj_ranker is None:
        print("  [NYT/WSJ Ranker] Error: nyt_wsj_rss_ranker.py could not be imported.")
        return []
    try:
        result = run_nyt_wsj_ranker(
            target_date=date,
            max_links=int(settings.get("nyt_wsj_max_links", 20)),
            output_dir=REPO_ROOT / settings.get("ranker_output_dir", "output/ranker_diagnostics"),
            write_files=True,
        )
        return result.get("selected", [])
    except Exception as e:
        print(f"  [NYT/WSJ Ranker] Error: {e}")
        return []


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
    nyt_wsj = _run_nyt_wsj_ranker(date, settings)

    print("  [3/7] Blog / research ranker...")
    blogs = _run_blog_ranker(date, settings)

    data = {
        "hn": hn,
        "nyt_wsj": nyt_wsj,
        "blogs": blogs,
        "linkedin": [],
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
