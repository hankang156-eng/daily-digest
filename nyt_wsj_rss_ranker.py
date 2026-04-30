#!/usr/bin/env python3
"""Rank NYT and WSJ RSS items for the daily digest."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import re
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
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "smid", "ref", "mod", "cid", "campaign_id", "emc", "partner",
}


DEFAULT_FEEDS: list[dict[str, Any]] = [
    {"publication": "NYT", "section": "U.S.", "category": "Politics / U.S.", "url": "https://rss.nytimes.com/services/xml/rss/nyt/US.xml", "section_url": "https://www.nytimes.com/section/us"},
    {"publication": "NYT", "section": "Politics", "category": "Politics / U.S.", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "section_url": "https://www.nytimes.com/section/politics"},
    {"publication": "NYT", "section": "Business", "category": "Business / Economy / Markets", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "section_url": "https://www.nytimes.com/section/business"},
    {"publication": "NYT", "section": "Technology", "category": "Technology / AI", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "section_url": "https://www.nytimes.com/section/technology"},
    {"publication": "NYT", "section": "Economy", "category": "Business / Economy / Markets", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", "section_url": "https://www.nytimes.com/section/business/economy"},
    {"publication": "NYT", "section": "Energy & Environment", "category": "Climate / Energy / Infrastructure", "url": "https://rss.nytimes.com/services/xml/rss/nyt/EnergyEnvironment.xml", "section_url": "https://www.nytimes.com/section/climate"},
    {"publication": "NYT", "section": "DealBook", "category": "Business / Economy / Markets", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Dealbook.xml", "section_url": "https://www.nytimes.com/section/business/dealbook"},
    {"publication": "NYT", "section": "Personal Technology", "category": "Wellness / Personal finance / Personal tech", "url": "https://rss.nytimes.com/services/xml/rss/nyt/PersonalTech.xml", "section_url": "https://www.nytimes.com/section/technology/personaltech"},
    {"publication": "NYT", "section": "Your Money", "category": "Wellness / Personal finance / Personal tech", "url": "https://rss.nytimes.com/services/xml/rss/nyt/YourMoney.xml", "section_url": "https://www.nytimes.com/section/your-money"},
    {"publication": "NYT", "section": "Ask Well", "category": "Wellness / Personal finance / Personal tech", "url": "", "section_url": "https://www.nytimes.com/column/ask-well"},
    {"publication": "NYT", "section": "Artificial Intelligence", "category": "Technology / AI", "url": "", "section_url": "https://www.nytimes.com/spotlight/artificial-intelligence"},
    {"publication": "NYT", "section": "Opinion", "category": "Opinion / Analysis", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Opinion.xml", "section_url": "https://www.nytimes.com/section/opinion"},
    {"publication": "NYT", "section": "Sunday Opinion", "category": "Opinion / Analysis", "url": "https://rss.nytimes.com/services/xml/rss/nyt/SundayReview.xml", "section_url": "https://www.nytimes.com/section/opinion/sunday"},
    {"publication": "WSJ", "section": "Business", "category": "Business / Economy / Markets", "url": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml", "section_url": "https://www.wsj.com/business"},
    {"publication": "WSJ", "section": "Markets", "category": "Business / Economy / Markets", "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "section_url": "https://www.wsj.com/finance"},
    {"publication": "WSJ", "section": "Economy", "category": "Business / Economy / Markets", "url": "https://feeds.a.dj.com/rss/WSJcomUSEconomy.xml", "section_url": "https://www.wsj.com/economy"},
    {"publication": "WSJ", "section": "Technology", "category": "Technology / AI", "url": "https://feeds.a.dj.com/rss/RSSWSJD.xml", "section_url": "https://www.wsj.com/tech"},
    {"publication": "WSJ", "section": "Opinion", "category": "Opinion / Analysis", "url": "https://feeds.a.dj.com/rss/RSSOpinion.xml", "section_url": "https://www.wsj.com/news/opinion"},
    {"publication": "WSJ", "section": "Deals / M&A", "category": "Business / Economy / Markets", "url": "", "section_url": "https://www.wsj.com/business/deals"},
    {"publication": "WSJ", "section": "Heard on the Street", "category": "Opinion / Analysis", "url": "", "section_url": "https://www.wsj.com/news/heard-on-the-street"},
    {"publication": "WSJ", "section": "Management / Workplace", "category": "Business / Economy / Markets", "url": "", "section_url": "https://www.wsj.com/business/management"},
    {"publication": "WSJ", "section": "C-Suite", "category": "Business / Economy / Markets", "url": "", "section_url": "https://www.wsj.com/business/c-suite"},
    {"publication": "WSJ", "section": "Energy / Climate", "category": "Climate / Energy / Infrastructure", "url": "", "section_url": "https://www.wsj.com/business/energy-oil"},
]

SECTION_WEIGHTS = {
    "Politics": 22, "U.S.": 20, "Business": 22, "Economy": 23,
    "Technology": 23, "Artificial Intelligence": 28, "Energy & Environment": 25,
    "DealBook": 19, "Personal Technology": 14, "Your Money": 14, "Ask Well": 10,
    "Sunday Opinion": 16, "Opinion": 12, "Markets": 24, "Deals / M&A": 24,
    "Heard on the Street": 20, "Management / Workplace": 18, "C-Suite": 18,
    "Energy / Climate": 23,
}

KEYWORD_GROUPS = {
    "Technology / AI": (24, ["artificial intelligence", " a.i.", " ai ", "openai", "chatgpt", "agent", "automation", "enterprise software", "machine learning", "semiconductor", "chip", "cloud", "data center"]),
    "Climate / Energy / Infrastructure": (22, ["clean energy", "grid", "electricity", "utility", "utilities", "climate", "solar", "battery", "nuclear", "infrastructure", "energy transition"]),
    "Business / Economy / Markets": (21, ["inflation", "interest rate", "federal reserve", "labor market", "productivity", "earnings", "stocks", "bond", "markets", "capital", "merger", "acquisition", "private equity", "venture capital", "startup"]),
    "Corporate Strategy": (18, ["strategy", "business model", "pricing", "management", "chief executive", "c-suite", "competition", "enterprise", "corporate"]),
    "Regulation / Policy": (17, ["regulation", "antitrust", "lawsuit", "supreme court", "congress", "policy", "tariff", "ftc", "justice department"]),
    "Personal Utility": (12, ["personal finance", "retirement", "health", "wellness", "consumer", "household", "mortgage", "insurance"]),
}

DIVERSITY_CAPS = {
    "Politics / U.S.": 4,
    "Business / Economy / Markets": 6,
    "Technology / AI": 5,
    "Climate / Energy / Infrastructure": 3,
    "Opinion / Analysis": 4,
    "Wellness / Personal finance / Personal tech": 3,
}


@dataclass
class Candidate:
    title: str
    canonical_url: str
    original_url: str
    publication: str
    sections: list[str]
    published_date: str
    author: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = "General"
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    topic_cluster: str = "General"
    reading_mode: str = "Skim"
    source_role: str = "Core news"
    reason: str = ""
    selected: bool = False
    exclusion_reason: str = ""
    publications: set[str] = field(default_factory=set)


def get_target_date(days_back: int = 1) -> dt.date:
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


def load_config(path: Path | None = None) -> dict[str, Any]:
    config = load_json(path or SCRIPT_DIR / "config.json", {})
    return config.get("nyt_wsj_ranker", config)


def read_suppressed(output_dir: Path) -> set[str]:
    urls = set()
    for path in (SCRIPT_DIR / "read_urls.txt", output_dir / "read_urls.txt"):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.add(canonicalize_url(line))
    return urls


def discover_feed(section_url: str) -> str:
    if not section_url or not HAS_REQUESTS or not HAS_BS4:
        return ""
    try:
        response = requests.get(section_url, headers=HTTP_HEADERS, timeout=12)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all("link", href=True):
            rel = " ".join(tag.get("rel", [])).lower()
            type_ = (tag.get("type") or "").lower()
            if "alternate" in rel and ("rss" in type_ or "atom" in type_ or "xml" in type_):
                return urljoin(section_url, tag["href"])
    except Exception as exc:
        logging.info("Feed discovery failed for %s: %s", section_url, exc)
    return ""


def fetch_feed(feed: dict[str, Any], stats: dict[str, int]) -> list[Candidate]:
    if not HAS_FEEDPARSER:
        logging.error("feedparser is not installed")
        stats["feed_failures"] += 1
        return []
    url = feed.get("url") or discover_feed(feed.get("section_url", ""))
    if not url:
        logging.info("No feed URL for %s %s", feed.get("publication"), feed.get("section"))
        stats["feed_failures"] += 1
        return []
    try:
        if HAS_REQUESTS:
            response = requests.get(url, headers=HTTP_HEADERS, timeout=20)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
        else:
            parsed = feedparser.parse(url, request_headers=HTTP_HEADERS)
        entries = getattr(parsed, "entries", [])
        if getattr(parsed, "bozo", False) and not entries:
            raise ValueError(getattr(parsed, "bozo_exception", "feed parse failed"))
        stats["feeds_fetched"] += 1
    except Exception as exc:
        logging.warning("[%s %s] %s", feed.get("publication"), feed.get("section"), exc)
        stats["feed_failures"] += 1
        return []

    candidates = []
    for entry in entries:
        title = clean_text(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        pub_date = parse_entry_date(entry)
        tags = [clean_text(tag.get("term", "")) for tag in entry.get("tags", []) if tag.get("term")]
        candidates.append(Candidate(
            title=title,
            canonical_url=canonicalize_url(link),
            original_url=link,
            publication=feed["publication"],
            sections=[feed["section"]],
            published_date=pub_date.isoformat() if pub_date else "",
            author=clean_text(entry.get("author", "")),
            summary=clean_text(entry.get("summary", ""))[:500],
            tags=tags,
            category=feed.get("category", "General"),
            publications={feed["publication"]},
        ))
    return candidates


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def merge_candidates(candidates: list[Candidate]) -> list[Candidate]:
    merged: list[Candidate] = []
    for candidate in candidates:
        match = None
        for existing in merged:
            if candidate.canonical_url == existing.canonical_url or title_similarity(candidate.title, existing.title) >= 0.91:
                match = existing
                break
        if not match:
            merged.append(candidate)
            continue
        match.sections = sorted(set(match.sections + candidate.sections))
        match.publications.add(candidate.publication)
        if candidate.publication not in match.publication:
            match.publication = " + ".join(sorted(match.publications))
        if len(candidate.summary) > len(match.summary):
            match.summary = candidate.summary
        if not match.published_date:
            match.published_date = candidate.published_date
    return merged


def days_old(candidate: Candidate, target_date: dt.date) -> int:
    if not candidate.published_date:
        return 0
    try:
        return max(0, (target_date - dt.date.fromisoformat(candidate.published_date)).days)
    except ValueError:
        return 0


def keyword_score(text: str) -> tuple[float, str]:
    best_group = "General"
    best_score = 0.0
    total = 0.0
    lower = f" {text.lower()} "
    for group, (weight, terms) in KEYWORD_GROUPS.items():
        hits = sum(1 for term in terms if term in lower)
        if hits:
            score = min(weight, weight * (0.45 + 0.25 * hits))
            total += score
            if score > best_score:
                best_group = group
                best_score = score
    return min(total, 45), best_group


def is_opinion(candidate: Candidate) -> bool:
    text = " ".join(candidate.sections + [candidate.title]).lower()
    signals = ("opinion", "heard on the street", "essay", "column", "commentary", "review", "the case for", "the case against")
    return any(signal in text for signal in signals)


def classify(candidate: Candidate, topic_hint: str) -> None:
    if candidate.category != "General":
        candidate.topic_cluster = candidate.category
    elif topic_hint != "General":
        candidate.topic_cluster = topic_hint
    if is_opinion(candidate):
        candidate.topic_cluster = "Opinion / Analysis"
        candidate.source_role = "Opinion/argument"
    elif candidate.publication == "WSJ" and candidate.topic_cluster in {"Business / Economy / Markets", "Technology / AI"}:
        candidate.source_role = "Market/corporate lens"
    elif candidate.publication == "NYT" and candidate.topic_cluster in {"Politics / U.S.", "Regulation / Policy"}:
        candidate.source_role = "Policy/social lens"
    elif candidate.topic_cluster == "Wellness / Personal finance / Personal tech":
        candidate.source_role = "Personal utility"
    elif candidate.topic_cluster in {"Technology / AI", "Business / Economy / Markets"}:
        candidate.source_role = "Strategic signal"
    candidate.reading_mode = "Read deeply" if candidate.score >= 55 else "Skim" if candidate.score >= 35 else "Save for weekly review"


def score_candidate(candidate: Candidate, target_date: dt.date) -> None:
    section = candidate.sections[0] if candidate.sections else ""
    section_score = max(SECTION_WEIGHTS.get(s, 10) for s in candidate.sections or [section])
    text = " ".join([candidate.title, candidate.summary, " ".join(candidate.sections), " ".join(candidate.tags)])
    relevance, topic_hint = keyword_score(text)
    age = days_old(candidate, target_date)
    recency = 14 if age == 0 else max(-12, 8 - age * 5)
    cross = min(8, max(0, len(candidate.sections) - 1) * 3 + (4 if len(candidate.publications) > 1 else 0))
    differentiation = 0
    lower = text.lower()
    if "WSJ" in candidate.publications and any(term in lower for term in ("market", "earnings", "valuation", "investor", "m&a", "merger", "capital", "deal")):
        differentiation += 8
    if "NYT" in candidate.publications and any(term in lower for term in ("policy", "health", "social", "law", "court", "politics", "regulation")):
        differentiation += 6
    opinion_penalty = -8 if is_opinion(candidate) and relevance < 12 else 0
    candidate.score_breakdown = {
        "section": section_score,
        "keyword": relevance,
        "recency": recency,
        "cross_section": cross,
        "source_differentiation": differentiation,
        "opinion": opinion_penalty,
    }
    candidate.score = round(sum(candidate.score_breakdown.values()), 2)
    classify(candidate, topic_hint)
    reason_bits = []
    if relevance:
        reason_bits.append(f"matches {topic_hint.lower()}")
    if differentiation:
        reason_bits.append(candidate.source_role.lower())
    if len(candidate.sections) > 1:
        reason_bits.append("appears across sections")
    candidate.reason = "; ".join(reason_bits) or f"strong {section or candidate.publication} signal"


def select_candidates(candidates: list[Candidate], max_links: int) -> list[Candidate]:
    selected: list[Candidate] = []
    topic_counts: dict[str, int] = {}
    pub_counts = {"WSJ": 0, "NYT": 0}
    opinion_count = 0
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        pub = "WSJ" if "WSJ" in candidate.publications and "NYT" not in candidate.publications else "NYT"
        topic = candidate.topic_cluster
        if pub == "WSJ" and pub_counts["WSJ"] >= 5:
            candidate.exclusion_reason = "WSJ daily cap"
            continue
        if is_opinion(candidate) and opinion_count >= 4:
            candidate.exclusion_reason = "opinion cap"
            continue
        if topic_counts.get(topic, 0) >= DIVERSITY_CAPS.get(topic, 20):
            candidate.exclusion_reason = f"{topic} cap"
            continue
        candidate.selected = True
        selected.append(candidate)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        pub_counts[pub] = pub_counts.get(pub, 0) + 1
        opinion_count += 1 if is_opinion(candidate) else 0
        if len(selected) >= max_links:
            break
    for candidate in candidates:
        if not candidate.selected and not candidate.exclusion_reason:
            candidate.exclusion_reason = "below selected score threshold"
    return selected


def executive_summary(selected: list[Candidate]) -> list[str]:
    clusters: dict[str, int] = {}
    for item in selected:
        clusters[item.topic_cluster] = clusters.get(item.topic_cluster, 0) + 1
    bullets = [f"{cluster}: {count} selected item{'s' if count != 1 else ''}." for cluster, count in sorted(clusters.items(), key=lambda kv: kv[1], reverse=True)[:6]]
    return bullets or ["No NYT/WSJ articles matched the ranking criteria."]


def candidate_to_article(candidate: Candidate) -> dict[str, Any]:
    source = candidate.publication
    section = " / ".join(candidate.sections)
    return {
        "title": candidate.title,
        "url": candidate.original_url,
        "source": source,
        "outlet": source if source in {"NYT", "WSJ"} else "",
        "section": section,
        "date": candidate.published_date,
        "summary": candidate.summary,
        "topic": candidate.topic_cluster,
        "category": "opinion" if is_opinion(candidate) else "news",
        "score": candidate.score,
        "reason": candidate.reason,
        "reading_mode": candidate.reading_mode,
    }


def write_outputs(selected: list[Candidate], candidates: list[Candidate], target_date: dt.date, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"nyt_wsj_briefing_{target_date.isoformat()}.md"
    csv_path = output_dir / f"nyt_wsj_candidates_{target_date.isoformat()}.csv"
    lines = [f"# NYT / WSJ Briefing - {target_date.isoformat()}", ""]
    lines += ["## Executive Summary", ""]
    lines += [f"- {line}" for line in executive_summary(selected)]
    lines += ["", "## Selected Reading List", ""]
    current = None
    for rank, item in enumerate(selected, 1):
        if item.topic_cluster != current:
            current = item.topic_cluster
            lines += [f"### {current}", ""]
        lines.append(f"{rank}. [{item.title}]({item.original_url})")
        lines.append(f"   - {item.publication} · {' / '.join(item.sections)} · {item.published_date or 'date unknown'}")
        lines.append(f"   - Score {item.score} · {item.reading_mode} · {item.reason}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "canonical_url", "original_url", "publication", "sections",
            "published_date", "author", "summary", "score", "score_breakdown",
            "topic_cluster", "reading_mode", "selected_true_false", "exclusion_reason_if_any",
        ])
        writer.writeheader()
        for item in sorted(candidates, key=lambda candidate: candidate.score, reverse=True):
            writer.writerow({
                "title": item.title,
                "canonical_url": item.canonical_url,
                "original_url": item.original_url,
                "publication": item.publication,
                "sections": "; ".join(item.sections),
                "published_date": item.published_date,
                "author": item.author,
                "summary": item.summary,
                "score": item.score,
                "score_breakdown": json.dumps(item.score_breakdown, sort_keys=True),
                "topic_cluster": item.topic_cluster,
                "reading_mode": item.reading_mode,
                "selected_true_false": item.selected,
                "exclusion_reason_if_any": item.exclusion_reason,
            })


def run_ranker(
    target_date: dt.date | None = None,
    max_links: int = 20,
    days_back: int = 1,
    output_dir: str | Path = "output",
    include_nyt: bool = True,
    include_wsj: bool = True,
    debug: bool = False,
    write_files: bool = True,
) -> dict[str, Any]:
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, format="%(message)s")
    target_date = target_date or get_target_date(days_back)
    out_dir = Path(output_dir)
    config = load_config()
    feeds = config.get("feeds", DEFAULT_FEEDS)
    if not include_nyt:
        feeds = [feed for feed in feeds if feed.get("publication") != "NYT"]
    if not include_wsj:
        feeds = [feed for feed in feeds if feed.get("publication") != "WSJ"]
    stats = {"feeds_fetched": 0, "feed_failures": 0, "raw_articles": 0}
    suppressed = read_suppressed(out_dir)
    raw: list[Candidate] = []
    for feed in feeds:
        raw.extend(fetch_feed(feed, stats))
    stats["raw_articles"] = len(raw)
    candidates = [item for item in merge_candidates(raw) if item.canonical_url not in suppressed]
    for item in candidates:
        score_candidate(item, target_date)
    selected = select_candidates(candidates, max_links)
    if write_files and (stats["raw_articles"] or candidates):
        write_outputs(selected, candidates, target_date, out_dir)
    print(f"NYT/WSJ feeds fetched: {stats['feeds_fetched']}")
    print(f"NYT/WSJ feed failures: {stats['feed_failures']}")
    print(f"NYT/WSJ raw articles: {stats['raw_articles']}")
    print(f"NYT/WSJ after dedupe: {len(candidates)}")
    print(f"NYT/WSJ selected: {len(selected)}")
    for rank, item in enumerate(selected[:20], 1):
        print(f"{rank:2}. {item.publication} {item.score:5.1f} - {item.title} ({item.reason})")
    return {
        "selected": [candidate_to_article(item) for item in selected],
        "candidates": candidates,
        "stats": {**stats, "deduped": len(candidates), "selected": len(selected)},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank NYT and WSJ RSS articles.")
    parser.add_argument("--max-links", type=int, default=20)
    parser.add_argument("--days-back", type=int, default=1)
    parser.add_argument("--date", type=str)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--include-wsj", action="store_true", default=True)
    parser.add_argument("--exclude-wsj", action="store_true")
    parser.add_argument("--include-nyt", action="store_true", default=True)
    parser.add_argument("--exclude-nyt", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = dt.date.fromisoformat(args.date) if args.date else None
    max_links = 50 if args.weekly and args.max_links == 20 else args.max_links
    days_back = 7 if args.weekly else args.days_back
    run_ranker(
        target_date=target_date,
        max_links=max_links,
        days_back=days_back,
        output_dir=args.output_dir,
        include_nyt=not args.exclude_nyt,
        include_wsj=not args.exclude_wsj,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
