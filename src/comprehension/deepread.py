"""Deep read: fetch full text for the few promoted items and write `Context:`.

Two-stage by design. Triage runs on cheap metadata across every item; only the
handful it promotes get fetched and read in full. That is where comprehension
actually happens, so that is where the tokens go.

`Context:` is deliberately not a second summary. The digest already prints
`Overview:` (what the piece covers, from metadata). This supplies what the
article assumes the reader already knows.

NYT is abstract-only: nytimes.com returns 403 to the scraper, which is why the
digest gets NYT word counts from the Article Search API instead of the page.
"""

import re
from urllib.parse import urlparse

from .paths import RANKER_OUTPUT_DIR
from .store import load_json, write_json

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    requests = None

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    BeautifulSoup = None

TEXT_CACHE_FILE = RANKER_OUTPUT_DIR / "article_text_cache.json"

# Hosts that reject the scraper outright; do not waste a request on them.
BLOCKED_HOSTS = {"nytimes.com", "www.nytimes.com", "wsj.com", "www.wsj.com", "ft.com", "www.ft.com"}

# Truncating here keeps the prompt deterministic across runs (so the LLM result
# cache actually hits) and bounds per-item token spend.
MAX_TEXT_CHARS = 12000
MIN_USEFUL_CHARS = 400
DEFAULT_MAX_DEEP_READS = 8
FETCH_TIMEOUT = 20

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

_NOISE_TAGS = ("script", "style", "noscript", "svg", "form", "nav", "header", "footer", "aside")

CONTEXT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "context"],
                "properties": {
                    "index": {"type": "integer"},
                    "context": {"type": "string"},
                },
            },
        },
    },
}

CONTEXT_SYSTEM = """\
For each item, write one paragraph of context for a reader who is following the \
story but is not a specialist in it.

This is not a summary. The reader already has a summary of what the piece covers. \
Spend the paragraph on what the piece *assumes*:

  - the background needed to place this development in its story
  - the one or two pieces of jargon or shorthand that are load-bearing here, \
decoded in plain language
  - why this is happening now rather than a year ago
  - what would count as the next real move

Rules:
  - 70-110 words, one paragraph, plain prose. No markdown, no lists, no headers.
  - Ground it in the text provided. You may explain established background the \
text assumes, but never invent events, numbers, quotes, or attributions.
  - Do not open with "This article..." or restate the headline. Start with the \
context itself.
  - If the provided text is too thin to support real context, say so plainly in \
one short sentence rather than padding.
  - Return one entry per item, keyed by the item's index.
"""


def is_fetchable(url):
    if not url or not HAS_REQUESTS:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and host not in BLOCKED_HOSTS


def extract_text(html_text, max_chars=MAX_TEXT_CHARS):
    """Readable body text. Mirrors daily_digest.article_word_count_from_html's cleaning."""
    if not html_text:
        return ""
    if HAS_BS4:
        soup = BeautifulSoup(html_text, "html.parser")
        for node in soup(list(_NOISE_TAGS)):
            node.decompose()
        article = soup.find("article")
        text = article.get_text(" ", strip=True) if article else ""
        if len(text) < MIN_USEFUL_CHARS:
            # An <article> wrapper is sometimes empty or JS-populated; the body
            # still carries the prose, so prefer it over returning nothing.
            text = (soup.body or soup).get_text(" ", strip=True) or text
    else:
        cleaned = re.sub(
            r"<(" + "|".join(_NOISE_TAGS) + r")\b.*?</\1>", " ", html_text, flags=re.I | re.S
        )
        text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def fetch_article_text(url, cache=None, max_chars=MAX_TEXT_CHARS, timeout=FETCH_TIMEOUT, verbose=True):
    """Fetched-and-extracted body text, or "" when unavailable. Cached by URL."""
    if cache is not None and url in cache:
        return cache[url]
    if not is_fetchable(url):
        return ""
    try:
        response = requests.get(url, timeout=timeout, headers=HTTP_HEADERS)
        response.raise_for_status()
        text = extract_text(response.text, max_chars)
    except Exception as e:
        if verbose:
            print(f"  [Comprehension] Could not fetch {url}: {e}")
        text = ""
    if cache is not None and text:
        cache[url] = text
    return text


def select_promoted(promoted_keys, items_by_key, max_deep_reads=DEFAULT_MAX_DEEP_READS, verbose=True):
    """Promoted items, deduped and capped, fetchable ones first.

    Triage cannot know which hosts block scraping, and it tends to promote NYT
    items because they score highest and are marked "Read deeply" - exactly the
    source whose full text is unavailable. Ordering fetchable items first spends
    the deep-read budget where real text exists, while still leaving blocked
    items eligible if budget remains (their Context is then written from the
    summary, which is the best available).
    """
    selected, seen = [], set()
    for key in promoted_keys or []:
        item = items_by_key.get(key)
        if not item or key in seen:
            continue
        seen.add(key)
        selected.append(item)
    selected.sort(key=lambda item: not is_fetchable(item.get("url")))
    if len(selected) > max_deep_reads:
        if verbose:
            print(
                f"  [Comprehension] {len(selected)} items promoted; deep-reading the first "
                f"{max_deep_reads}. Skipped: "
                + ", ".join(item.get("title", "?") for item in selected[max_deep_reads:])
            )
        selected = selected[:max_deep_reads]
    return selected


def build_prompt(entries, thread_names=None):
    """entries: [(item, text)] — text may be "" when the source blocks scraping."""
    thread_names = thread_names or {}
    parts = []
    for index, (item, text) in enumerate(entries, 1):
        parts.append(f"## Item {index}: {item.get('title', '')}")
        parts.append(f"Source: {item.get('source', '')}")
        thread = thread_names.get(item.get("item_key"))
        if thread:
            parts.append(f"Belongs to the thread: {thread}")
        if item.get("prose"):
            parts.append(f"Existing summary: {item['prose']}")
        if text:
            parts.append("")
            parts.append("Full text:")
            parts.append(text)
        else:
            parts.append("")
            parts.append(
                "Full text unavailable (this source blocks automated fetching); "
                "work from the summary above only."
            )
        parts.append("")
    parts.append(f"Write the context paragraph for each of the {len(entries)} items above.")
    return "\n".join(parts)


def context_for_items(
    promoted_keys,
    items_by_key,
    client,
    thread_names=None,
    max_deep_reads=DEFAULT_MAX_DEEP_READS,
    text_cache_path=TEXT_CACHE_FILE,
    text_cache=None,
    effort=None,
    verbose=True,
):
    """Return {item_key: context_paragraph} for the promoted items."""
    selected = select_promoted(promoted_keys, items_by_key, max_deep_reads, verbose)
    if not selected:
        return {}

    owns_cache = text_cache is None
    if owns_cache:
        text_cache = load_json(text_cache_path, {}) if text_cache_path else {}

    entries = [
        (item, fetch_article_text(item.get("url"), cache=text_cache, verbose=verbose))
        for item in selected
    ]
    if owns_cache and text_cache_path:
        write_json(text_cache_path, text_cache)

    thin = sum(1 for _, text in entries if len(text) < MIN_USEFUL_CHARS)
    if thin and verbose:
        print(f"  [Comprehension] {thin}/{len(entries)} promoted items had no usable full text.")

    response = client.complete(
        build_prompt(entries, thread_names), system=CONTEXT_SYSTEM, schema=CONTEXT_SCHEMA, effort=effort
    )
    if not isinstance(response, dict):
        return {}

    contexts = {}
    for entry in response.get("items") or []:
        index = entry.get("index")
        if not isinstance(index, int) or not 1 <= index <= len(entries):
            continue
        context = str(entry.get("context") or "").strip()
        if context:
            contexts[entries[index - 1][0]["item_key"]] = context
    return contexts


def thread_names_for(registry, digest_date):
    """{item_key: thread name} for items filed on this date, to orient each context."""
    names = {}
    for thread in registry.get("threads", []):
        for entry in thread.get("items", []):
            if entry.get("digest_date") == digest_date:
                names[entry.get("item_key")] = thread.get("name", "")
    return names
