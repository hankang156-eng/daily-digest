#!/usr/bin/env python3
"""Rank blog and research posts for the daily digest."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

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
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source"}

DEFAULT_SOURCES: list[dict[str, Any]] = [
    {"name": "MIT IDE", "category": "MIT Research & Insights", "url": "https://ide.mit.edu/latest-insights/", "feed_url": "https://ide.mit.edu/feed/", "ingestion_type": "rss_then_scrape", "source_weight": 24, "notes": "research commercialization"},
    {"name": "MIT Shaping Work", "category": "MIT Research & Insights", "url": "https://shapingwork.mit.edu/research/", "feed_url": "https://shapingwork.mit.edu/feed/", "ingestion_type": "rss_then_scrape", "source_weight": 24, "notes": "future of work"},
    {"name": "Krebs on Security", "category": "Security & Privacy", "url": "https://krebsonsecurity.com/", "feed_url": "https://krebsonsecurity.com/feed/", "ingestion_type": "rss", "source_weight": 23, "notes": "security"},
    {"name": "Troy Hunt", "category": "Security & Privacy", "url": "https://www.troyhunt.com/", "feed_url": "https://feeds.feedburner.com/TroyHunt", "ingestion_type": "rss", "source_weight": 22, "notes": "security"},
    {"name": "Simon Willison", "category": "Tech & Engineering", "url": "https://simonwillison.net/", "feed_url": "https://simonwillison.net/atom/everything/", "ingestion_type": "atom", "source_weight": 25, "notes": "AI and software"},
    {"name": "Dan Luu", "category": "Tech & Engineering", "url": "https://danluu.com/", "feed_url": "https://danluu.com/atom.xml", "ingestion_type": "atom", "source_weight": 23, "notes": "durable engineering"},
    {"name": "Tonsky.me", "category": "Tech & Engineering", "url": "https://tonsky.me/", "feed_url": "https://tonsky.me/blog/atom.xml", "ingestion_type": "atom", "source_weight": 18, "notes": "engineering"},
    {"name": "Paul Graham", "category": "Tech & Engineering", "url": "http://paulgraham.com/articles.html", "feed_url": "", "ingestion_type": "scrape", "source_weight": 20, "notes": "cache detects new essays"},
    {"name": "Gwern.net", "category": "Tech & Engineering", "url": "https://gwern.net/", "feed_url": "https://gwern.net/feed/daily", "ingestion_type": "rss", "source_weight": 19, "notes": "research"},
    {"name": "Lemire.me", "category": "Tech & Engineering", "url": "https://lemire.me/blog/", "feed_url": "https://lemire.me/blog/feed/", "ingestion_type": "rss", "source_weight": 19, "notes": "performance and programming"},
    {"name": "Neal.fun", "category": "Tech & Engineering", "url": "https://neal.fun/", "feed_url": "", "ingestion_type": "rss_then_scrape", "source_weight": 16, "notes": "cache detects new projects"},
    {"name": "MIT Sloan Review", "category": "Strategy & Craft", "url": "https://sloanreview.mit.edu/", "feed_url": "https://sloanreview.mit.edu/feed/", "ingestion_type": "rss", "source_weight": 23, "notes": "business strategy"},
    {"name": "Daring Fireball", "category": "Strategy & Craft", "url": "https://daringfireball.net/", "feed_url": "https://daringfireball.net/feeds/main", "ingestion_type": "rss", "source_weight": 17, "notes": "tech taste"},
    {"name": "Rachel by the Bay", "category": "Strategy & Craft", "url": "https://rachelbythebay.com/w/", "feed_url": "https://rachelbythebay.com/w/atom.xml", "ingestion_type": "atom", "source_weight": 17, "notes": "engineering craft"},
    {"name": "Shkspr.mobi", "category": "Strategy & Craft", "url": "https://shkspr.mobi/blog/", "feed_url": "https://shkspr.mobi/blog/feed/", "ingestion_type": "rss", "source_weight": 16, "notes": "open web and craft"},
]

CATEGORY_WEIGHTS = {
    "MIT Research & Insights": 22,
    "Security & Privacy": 21,
    "Tech & Engineering": 21,
    "Strategy & Craft": 18,
}

KEYWORD_GROUPS = {
    "AI / Agents / LLMs": (24, ["llm", "agent", "artificial intelligence", " ai ", " a.i.", "machine learning", "openai", "model", "inference", "prompt"]),
    "Enterprise / Automation": (20, ["enterprise", "workflow", "automation", "productivity", "tooling", "software as a service", "saas"]),
    "Security / Privacy": (22, ["security", "privacy", "breach", "authentication", "passkey", "password", "malware", "ransomware", "vulnerability"]),
    "Software Engineering": (21, ["programming", "database", "postgres", "python", "javascript", "infrastructure", "latency", "performance", "debugging", "api"]),
    "Future of Work": (18, ["future of work", "labor", "organization", "management", "workplace", "productivity", "skills"]),
    "Strategy / Business Models": (18, ["strategy", "business model", "startup", "venture capital", "market", "platform", "pricing"]),
    "Research / Innovation": (18, ["research", "commercialization", "innovation", "study", "paper", "experiment"]),
    "Writing / Craft / Taste": (14, ["essay", "writing", "craft", "taste", "design", "long-term", "systems thinking"]),
    "Climate / Energy": (14, ["climate", "energy", "grid", "electricity", "infrastructure"]),
}

DIVERSITY_CAPS = {
    "MIT Research & Insights": 4,
    "Security & Privacy": 4,
    "Tech & Engineering": 8,
    "Strategy & Craft": 5,
}


@dataclass
class BlogCandidate:
    title: str
    canonical_url: str
    original_url: str
    source: str
    category: str
    author: str = ""
    published_date: str = ""
    date_confidence: str = "high"
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    topic_cluster: str = "General"
    content_type: str = "Engineering note"
    reading_mode: str = "Skim"
    reason: str = ""
    selected: bool = False
    exclusion_reason: str = ""
    is_new_cache_item: bool = False


def target_date_from_args(days_back: int = 1) -> dt.date:
    return dt.date.today() - dt.timedelta(days=days_back)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    path = parsed.path.rstrip("/") or parsed.path
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), ""))


def parse_entry_date(entry: Any) -> dt.date | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value:
            try:
                return dt.date(value.tm_year, value.tm_mon, value.tm_mday)
            except Exception:
                pass
    return None


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_config() -> dict[str, Any]:
    config = load_json(SCRIPT_DIR / "config.json", {})
    return config.get("blog_ranker", config)


def read_suppressed(output_dir: Path) -> set[str]:
    urls = set()
    for path in (SCRIPT_DIR / "read_urls.txt", output_dir / "read_urls.txt"):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.add(canonicalize_url(line))
    return urls


def load_cache(output_dir: Path) -> dict[str, Any]:
    cache = load_json(SCRIPT_DIR / "blog_cache.json", {})
    local_cache = load_json(output_dir / "blog_cache.json", {})
    cache.setdefault("seen_urls", {})
    cache["seen_urls"].update(local_cache.get("seen_urls", {}))
    return cache


def save_cache(output_dir: Path, cache: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "blog_cache.json").write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def discover_feed(url: str) -> str:
    if not url or not HAS_REQUESTS or not HAS_BS4:
        return ""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=12)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all("link", href=True):
            rel = " ".join(tag.get("rel", [])).lower()
            type_ = (tag.get("type") or "").lower()
            if "alternate" in rel and ("rss" in type_ or "atom" in type_ or "xml" in type_):
                return urljoin(url, tag["href"])
    except Exception as exc:
        logging.info("Feed discovery failed for %s: %s", url, exc)
    return ""


def fetch_rss(source: dict[str, Any], stats: dict[str, int]) -> list[BlogCandidate]:
    if not HAS_FEEDPARSER:
        stats["feed_failures"] += 1
        logging.error("feedparser is not installed")
        return []
    feed_url = source.get("feed_url") or discover_feed(source.get("url", ""))
    if not feed_url:
        stats["feed_failures"] += 1
        return []
    try:
        if HAS_REQUESTS:
            response = requests.get(feed_url, headers=HTTP_HEADERS, timeout=20)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
        else:
            parsed = feedparser.parse(feed_url, request_headers=HTTP_HEADERS)
        entries = getattr(parsed, "entries", [])
        if getattr(parsed, "bozo", False) and not entries:
            raise ValueError(getattr(parsed, "bozo_exception", "feed parse failed"))
        stats["feed_successes"] += 1
    except Exception as exc:
        logging.warning("[%s] RSS error: %s", source["name"], exc)
        stats["feed_failures"] += 1
        return []

    items = []
    for entry in entries:
        title = clean_text(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        pub_date = parse_entry_date(entry)
        tags = [clean_text(tag.get("term", "")) for tag in entry.get("tags", []) if tag.get("term")]
        items.append(BlogCandidate(
            title=title,
            canonical_url=canonicalize_url(link),
            original_url=link,
            source=source["name"],
            category=source["category"],
            author=clean_text(entry.get("author", "")),
            published_date=pub_date.isoformat() if pub_date else "",
            date_confidence="high" if pub_date else "low",
            summary=clean_text(entry.get("summary", ""))[:500],
            tags=tags,
        ))
    return items


def scrape_links(source: dict[str, Any], stats: dict[str, int], max_items: int = 12) -> list[BlogCandidate]:
    if not HAS_REQUESTS or not HAS_BS4:
        stats["scrape_failures"] += 1
        return []
    try:
        response = requests.get(source["url"], headers=HTTP_HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        stats["scrape_successes"] += 1
    except Exception as exc:
        logging.warning("[%s] scrape error: %s", source["name"], exc)
        stats["scrape_failures"] += 1
        return []
    items = []
    for tag in soup.find_all("a", href=True):
        title = clean_text(tag.get_text(" ", strip=True))
        href = urljoin(source["url"], tag["href"])
        if len(title) < 6 or href.startswith("mailto:") or "#" in href:
            continue
        lower = title.lower()
        if lower.startswith(("home", "about", "subscribe", "newsletter", "privacy", "support", "read more")):
            continue
        if source["name"] == "Paul Graham" and "paulgraham.com" not in href:
            continue
        if source["name"] == "Neal.fun" and "neal.fun" not in href:
            continue
        items.append(BlogCandidate(
            title=title,
            canonical_url=canonicalize_url(href),
            original_url=href,
            source=source["name"],
            category=source["category"],
            date_confidence="low",
            content_type="New project" if source["name"] == "Neal.fun" else "Technical essay",
        ))
    time.sleep(0.2)
    return dedupe(items)[:max_items]


def fetch_source(source: dict[str, Any], stats: dict[str, int]) -> list[BlogCandidate]:
    kind = source.get("ingestion_type", "rss")
    items: list[BlogCandidate] = []
    if kind in {"rss", "atom", "rss_then_scrape"}:
        items = fetch_rss(source, stats)
    if not items and kind in {"scrape", "rss_then_scrape"}:
        items = scrape_links(source, stats)
    for item in items:
        item.score_breakdown["source_weight"] = float(source.get("source_weight", 15))
    return items


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedupe(items: list[BlogCandidate]) -> list[BlogCandidate]:
    out: list[BlogCandidate] = []
    for item in items:
        if any(item.canonical_url == existing.canonical_url or title_similarity(item.title, existing.title) >= 0.92 for existing in out):
            continue
        out.append(item)
    return out


def date_matches(item: BlogCandidate, target_date: dt.date, days_window: int, cache: dict[str, Any]) -> bool:
    if item.published_date:
        try:
            pub_date = dt.date.fromisoformat(item.published_date)
            return 0 <= (target_date - pub_date).days < days_window
        except ValueError:
            return False
    seen = cache.setdefault("seen_urls", {})
    first_seen = seen.get(item.canonical_url)
    if not first_seen:
        item.is_new_cache_item = True
        item.published_date = target_date.isoformat()
        item.date_confidence = "low"
        return True
    return False


def update_seen_cache(items: list[BlogCandidate], cache: dict[str, Any], seen_date: dt.date) -> None:
    seen = cache.setdefault("seen_urls", {})
    for item in items:
        seen.setdefault(item.canonical_url, seen_date.isoformat())


def keyword_score(text: str) -> tuple[float, str]:
    lower = f" {text.lower()} "
    total = 0.0
    best_group = "General"
    best_score = 0.0
    for group, (weight, terms) in KEYWORD_GROUPS.items():
        hits = sum(1 for term in terms if term in lower)
        if hits:
            score = min(weight, weight * (0.45 + 0.25 * hits))
            total += score
            if score > best_score:
                best_group = group
                best_score = score
    return min(total, 45), best_group


def classify(item: BlogCandidate, topic_hint: str) -> None:
    item.topic_cluster = topic_hint if topic_hint != "General" else item.category
    lower = f"{item.title} {item.summary}".lower()
    if item.source in {"MIT IDE", "MIT Shaping Work"}:
        item.content_type = "Research insight"
    elif item.category == "Security & Privacy":
        item.content_type = "Security alert"
    elif item.source == "Neal.fun":
        item.content_type = "New project"
    elif any(term in lower for term in ("essay", "why", "how", "deep dive", "technical")):
        item.content_type = "Technical essay" if item.category == "Tech & Engineering" else "Strategy analysis"
    elif item.category == "Strategy & Craft":
        item.content_type = "Craft essay"
    else:
        item.content_type = "Engineering note"
    item.reading_mode = "Read deeply" if item.score >= 56 else "Skim" if item.score >= 36 else "Save for weekly review"


def score_item(item: BlogCandidate, target_date: dt.date) -> None:
    text = " ".join([item.title, item.summary, item.source, item.category, " ".join(item.tags)])
    relevance, topic_hint = keyword_score(text)
    source_weight = item.score_breakdown.get("source_weight", 15)
    category_weight = CATEGORY_WEIGHTS.get(item.category, 12)
    if item.published_date:
        try:
            age = max(0, (target_date - dt.date.fromisoformat(item.published_date)).days)
        except ValueError:
            age = 0
    else:
        age = 0
    freshness = 15 if age == 0 else max(-10, 9 - age * 4)
    lower = text.lower()
    durable = 8 if any(term in lower for term in ("essay", "research", "analysis", "explainer", "paper", "report", "technical")) else 0
    tiny_penalty = -6 if len(item.summary) < 40 and item.source not in {"Paul Graham", "Neal.fun"} else 0
    low_date_penalty = -4 if item.date_confidence == "low" and not item.is_new_cache_item else 0
    item.score_breakdown.update({
        "source": source_weight,
        "category": category_weight,
        "keyword": relevance,
        "freshness": freshness,
        "depth": durable,
        "brevity": tiny_penalty,
        "date_confidence": low_date_penalty,
    })
    item.score = round(sum(item.score_breakdown.values()), 2)
    classify(item, topic_hint)
    reason_bits = []
    if relevance:
        reason_bits.append(f"matches {topic_hint.lower()}")
    if durable:
        reason_bits.append("durable read")
    if item.is_new_cache_item:
        reason_bits.append("newly seen low-frequency source item")
    item.reason = "; ".join(reason_bits) or f"strong {item.source} signal"


def select_items(items: list[BlogCandidate], max_links: int) -> list[BlogCandidate]:
    selected = []
    category_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for item in sorted(items, key=lambda candidate: candidate.score, reverse=True):
        if category_counts.get(item.category, 0) >= DIVERSITY_CAPS.get(item.category, 20):
            item.exclusion_reason = f"{item.category} cap"
            continue
        if source_counts.get(item.source, 0) >= 3 and len(items) > 8:
            item.exclusion_reason = "single-source cap"
            continue
        item.selected = True
        selected.append(item)
        category_counts[item.category] = category_counts.get(item.category, 0) + 1
        source_counts[item.source] = source_counts.get(item.source, 0) + 1
        if len(selected) >= max_links:
            break
    for item in items:
        if not item.selected and not item.exclusion_reason:
            item.exclusion_reason = "below selected score threshold"
    return selected


def executive_summary(selected: list[BlogCandidate], no_new_sources: list[str]) -> list[str]:
    if not selected:
        return ["No new blog or research posts matched the target date."]
    counts: dict[str, int] = {}
    for item in selected:
        counts[item.category] = counts.get(item.category, 0) + 1
    bullets = [f"{category}: {count} selected item{'s' if count != 1 else ''}." for category, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]]
    if no_new_sources:
        bullets.append(f"{len(no_new_sources)} sources had no target-date posts.")
    return bullets


def candidate_to_article(item: BlogCandidate) -> dict[str, Any]:
    topic = {
        "MIT Research & Insights": "Research",
        "Security & Privacy": "Security",
        "Tech & Engineering": "Technology",
        "Strategy & Craft": "Strategy",
    }.get(item.category, item.category)
    category = {
        "MIT Research & Insights": "research",
        "Security & Privacy": "tech",
        "Tech & Engineering": "tech",
        "Strategy & Craft": "long-form",
    }.get(item.category, "long-form")
    return {
        "title": item.title,
        "url": item.original_url,
        "source": item.source,
        "outlet": "",
        "section": item.category,
        "date": item.published_date,
        "summary": item.summary,
        "topic": topic,
        "category": category,
        "score": item.score,
        "reason": item.reason,
        "reading_mode": item.reading_mode,
        "date_confidence": item.date_confidence,
    }


def write_outputs(selected: list[BlogCandidate], candidates: list[BlogCandidate], no_new_sources: list[str], target_date: dt.date, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"blog_briefing_{target_date.isoformat()}.md"
    csv_path = output_dir / f"blog_candidates_{target_date.isoformat()}.csv"
    lines = [f"# Blog Briefing - {target_date.isoformat()}", ""]
    lines.append(f"*Sources checked: {len(DEFAULT_SOURCES)} · Posts found: {len(candidates)} · Posts selected: {len(selected)}*")
    lines += ["", "## Executive Summary", ""]
    lines += [f"- {line}" for line in executive_summary(selected, no_new_sources)]
    if no_new_sources:
        lines += ["", "## No New Posts", "", ", ".join(sorted(no_new_sources))]
    lines += ["", "## Selected Reading List", ""]
    current = None
    for rank, item in enumerate(selected, 1):
        if item.category != current:
            current = item.category
            lines += [f"### {current}", ""]
        date_note = item.published_date or "date unknown"
        if item.date_confidence != "high":
            date_note += f" ({item.date_confidence} confidence)"
        lines.append(f"{rank}. [{item.title}]({item.original_url})")
        lines.append(f"   - {item.source} · {date_note} · Score {item.score} · {item.reading_mode}")
        lines.append(f"   - {item.reason}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "canonical_url", "original_url", "source", "category", "author",
            "published_date", "date_confidence", "summary", "score", "score_breakdown",
            "topic_cluster", "content_type", "reading_mode", "selected_true_false",
            "exclusion_reason_if_any",
        ])
        writer.writeheader()
        for item in sorted(candidates, key=lambda candidate: candidate.score, reverse=True):
            writer.writerow({
                "title": item.title,
                "canonical_url": item.canonical_url,
                "original_url": item.original_url,
                "source": item.source,
                "category": item.category,
                "author": item.author,
                "published_date": item.published_date,
                "date_confidence": item.date_confidence,
                "summary": item.summary,
                "score": item.score,
                "score_breakdown": json.dumps(item.score_breakdown, sort_keys=True),
                "topic_cluster": item.topic_cluster,
                "content_type": item.content_type,
                "reading_mode": item.reading_mode,
                "selected_true_false": item.selected,
                "exclusion_reason_if_any": item.exclusion_reason,
            })


def run_ranker(
    target_date: dt.date | None = None,
    days_back: int = 1,
    weekly: bool = False,
    max_links: int = 20,
    output_dir: str | Path = "output",
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    debug: bool = False,
    refresh_cache: bool = False,
    write_files: bool = True,
) -> dict[str, Any]:
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, format="%(message)s")
    target_date = target_date or target_date_from_args(days_back)
    window = 7 if weekly else max(1, days_back)
    out_dir = Path(output_dir)
    config = load_config()
    sources = config.get("sources", DEFAULT_SOURCES)
    if include_categories:
        wanted = set(include_categories)
        sources = [source for source in sources if source.get("category") in wanted]
    if exclude_categories:
        blocked = set(exclude_categories)
        sources = [source for source in sources if source.get("category") not in blocked]
    stats = {"feed_successes": 0, "feed_failures": 0, "scrape_successes": 0, "scrape_failures": 0}
    cache = {"seen_urls": {}} if refresh_cache else load_cache(out_dir)
    suppressed = read_suppressed(out_dir)
    raw: list[BlogCandidate] = []
    source_hits: dict[str, int] = {}
    for source in sources:
        items = fetch_source(source, stats)
        raw.extend(items)
        source_hits[source["name"]] = len(items)
    update_seen_cache(raw, cache, target_date)
    candidates = [
        item for item in dedupe(raw)
        if item.canonical_url not in suppressed and date_matches(item, target_date, window, cache)
    ]
    no_new_sources = [source["name"] for source in sources if not any(item.source == source["name"] for item in candidates)]
    for item in candidates:
        score_item(item, target_date)
    selected = select_items(candidates, max_links)
    if write_files and (raw or candidates):
        write_outputs(selected, candidates, no_new_sources, target_date, out_dir)
        save_cache(out_dir, cache)
    print(f"Blog sources checked: {len(sources)}")
    print(f"Blog feed successes: {stats['feed_successes']}")
    print(f"Blog feed failures: {stats['feed_failures']}")
    print(f"Blog scrape successes: {stats['scrape_successes']}")
    print(f"Blog scrape failures: {stats['scrape_failures']}")
    print(f"Blog raw items found: {len(raw)}")
    print(f"Blog target-date items: {len(candidates)}")
    print(f"Blog selected: {len(selected)}")
    for rank, item in enumerate(selected[:20], 1):
        print(f"{rank:2}. {item.source} {item.score:5.1f} - {item.title} ({item.reason})")
    return {
        "selected": [candidate_to_article(item) for item in selected],
        "candidates": candidates,
        "stats": {**stats, "sources_checked": len(sources), "raw_items": len(raw), "target_date_items": len(candidates), "selected": len(selected), "source_hits": source_hits},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank blog and research posts.")
    parser.add_argument("--date", type=str)
    parser.add_argument("--days-back", type=int, default=1)
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--max-links", type=int, default=20)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--include-categories", nargs="*")
    parser.add_argument("--exclude-categories", nargs="*")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = dt.date.fromisoformat(args.date) if args.date else None
    max_links = 50 if args.weekly and args.max_links == 20 else args.max_links
    run_ranker(
        target_date=target_date,
        days_back=args.days_back,
        weekly=args.weekly,
        max_links=max_links,
        output_dir=args.output_dir,
        include_categories=args.include_categories,
        exclude_categories=args.exclude_categories,
        debug=args.debug,
        refresh_cache=args.refresh_cache,
    )


if __name__ == "__main__":
    main()
