"""Load digest items for triage, from either input the pipeline can offer.

Two readers, one normalized item shape:

  * `items_from_digest_json` - the lossless JSON contract written by
    daily_digest.dump_digest_json. Preferred; this is what live runs use.
  * `items_from_markdown` - the archived output/daily_md/digest_*.md pages. Used
    for backfill, since no JSON exists for days that ran before the contract
    landed. The Markdown carries the Overview/Abstract prose for every section
    including HN and Reddit, which the ranker CSVs do not.
"""

import datetime
import re
from pathlib import Path

from .paths import DAILY_MD_DIR, DIGEST_JSON_DIR

# Ordered because headings are matched by substring; first hit wins. Section
# headings have been renamed over the life of the archive ("Hacker News Top 16"
# -> "Yesterday's Top HackerNews", "NYT / WSJ Strategic Reading List" -> "Today
# on The New York Times", "Blogs & Craft" -> "Blog Posts"), so every historical
# variant has to map or a backfill silently drops whole days.
_MD_SECTION_GROUPS = (
    ("HackerNews", "hn"),
    ("Hacker News", "hn"),
    ("New York Times", "nyt_wsj"),
    ("NYT", "nyt_wsj"),
    ("Research", "research"),
    ("Blog", "blogs"),
    ("Reddit", "reddit"),
    ("LinkedIn", "linkedin"),
)

_ITEM_RE = re.compile(
    r"^\s*(?:\d+\.|-)\s+"
    r"(?:\*\*\[(?P<badge>[^\]]*)\]\*\*\s+)?"
    r"\[(?P<title>.+?)\]\((?P<url>(?:https?://|#)[^)\s]*)\)"
    r"(?P<rest>.*)$"
)
_ABSTRACT_RE = re.compile(r"^\s*-\s+\*\*Abstract:\*\*\s*(?P<text>.+)$")
_TLDR_RE = re.compile(r"^\s*-\s+\*\*Bot TL;DR:\*\*\s*(?P<text>.+)$")
_OVERVIEW_RE = re.compile(
    r"^\s*-\s+\*\*Overview(?:\s*\(Model:\s*(?P<model>[^)]*)\))?:\*\*\s*(?P<text>.+)$"
)
_SECTION_RE = re.compile(r"^###\s+(?P<label>.+?)\s*$")
_SUBGROUP_RE = re.compile(r"^####\s+(?P<label>.+?)\s*$")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HN_POINTS_RE = re.compile(r"(?P<pts>\d[\d,]*)\s+pts")
_SCORE_RE = re.compile(r"score\s+(?P<score>\d+(?:\.\d+)?)")
_DIGEST_FILE_RE = re.compile(r"digest_(?P<date>\d{4}-\d{2}-\d{2})\.md$")


def item_key(url, title):
    """Must stay identical to daily_digest.article_key so both readers agree.

    Pinned by a test rather than an import, to keep this module free of the
    2,600-line digest module.
    """
    url = (url or "").strip().rstrip("/")
    return url or (title or "").strip().lower()


def md_unescape(value):
    """Reverse daily_digest._md_escape."""
    return (
        str(value or "")
        .replace("\\|", "|")
        .replace("\\]", "]")
        .replace("\\[", "[")
        .replace("\\\\", "\\")
    )


def track_for(source, track_map=None):
    """Which corpus track an item belongs to. Sources default to ai-at-large."""
    if not track_map:
        return "ai-at-large"
    return track_map.get(source) or track_map.get((source or "").strip()) or "ai-at-large"


def normalize_item(raw, group="", digest_date="", track_map=None):
    """Collapse a digest item (JSON or Markdown) to the triage shape."""
    prose = (
        raw.get("article_overview")
        or raw.get("discussion_overview")
        or raw.get("abstract")
        or raw.get("bot_tldr")
        or raw.get("summary")
        or ""
    )
    source = raw.get("source") or raw.get("outlet") or ""
    return {
        "item_key": raw.get("item_key") or item_key(raw.get("url"), raw.get("title")),
        "title": (raw.get("title") or "").strip(),
        "url": raw.get("url") or "",
        "source": source,
        "outlet": raw.get("outlet") or "",
        "section": raw.get("topic_tag") or raw.get("section") or "",
        "group": raw.get("group") or group,
        "topic": raw.get("topic") or "",
        "category": raw.get("category") or "",
        "score": raw.get("score"),
        "prose": str(prose).strip(),
        "date": str(raw.get("date") or "")[:10],
        "digest_date": raw.get("digest_date") or digest_date,
        "track": raw.get("track") or track_for(source, track_map),
    }


# --- JSON contract -----------------------------------------------------------

def digest_json_path(digest_date, directory=DIGEST_JSON_DIR):
    return Path(directory) / f"digest_{_iso(digest_date)}.json"


def load_digest_json(digest_date, directory=DIGEST_JSON_DIR):
    import json

    path = digest_json_path(digest_date, directory)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [Comprehension] Error reading {path.name}: {e}")
        return None


def items_from_digest_json(payload, track_map=None, skip_groups=("linkedin",)):
    if not payload:
        return []
    digest_date = payload.get("digest_date", "")
    items = []
    for group, group_items in (payload.get("sections") or {}).items():
        if group in (skip_groups or ()):
            continue
        for raw in group_items or []:
            items.append(normalize_item(raw, group, digest_date, track_map))
    return items


# --- archived Markdown -------------------------------------------------------

def markdown_path(digest_date, directory=DAILY_MD_DIR):
    return Path(directory) / f"digest_{_iso(digest_date)}.md"


def available_markdown_dates(directory=DAILY_MD_DIR):
    directory = Path(directory)
    if not directory.is_dir():
        return []
    dates = []
    for path in directory.glob("digest_*.md"):
        match = _DIGEST_FILE_RE.search(path.name)
        if match:
            dates.append(match.group("date"))
    return sorted(dates)


def parse_markdown(text, digest_date="", track_map=None, skip_groups=("linkedin",)):
    """Extract items from a rendered digest Markdown page."""
    group = ""
    subgroup = ""
    items = []
    current = None

    for line in text.splitlines():
        section = _SECTION_RE.match(line)
        if section:
            group = _group_for_heading(section.group("label"))
            subgroup = ""
            current = None
            continue
        subheading = _SUBGROUP_RE.match(line)
        if subheading:
            subgroup = md_unescape(subheading.group("label"))
            current = None
            continue

        detail = _ABSTRACT_RE.match(line)
        if detail and current is not None:
            current["abstract"] = md_unescape(detail.group("text")).strip()
            continue
        detail = _TLDR_RE.match(line)
        if detail and current is not None:
            current["bot_tldr"] = md_unescape(detail.group("text")).strip()
            continue
        detail = _OVERVIEW_RE.match(line)
        if detail and current is not None:
            current["article_overview"] = md_unescape(detail.group("text")).strip()
            continue

        match = _ITEM_RE.match(line)
        if match and group:
            current = _item_from_md_match(match, group, subgroup)
            if current is not None:
                items.append(current)
            continue

        # A bare "  - Read deeply - reason" trailer on the item above.
        if current is not None and line.startswith("   -") and "](" not in line:
            current.setdefault("reason", md_unescape(line.strip()[2:]).strip())

    return [
        normalize_item(raw, raw.get("group", ""), digest_date, track_map)
        for raw in items
        if raw.get("group") not in (skip_groups or ())
    ]


def items_from_markdown(digest_date, directory=DAILY_MD_DIR, track_map=None):
    path = markdown_path(digest_date, directory)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  [Comprehension] Error reading {path.name}: {e}")
        return []
    return parse_markdown(text, _iso(digest_date), track_map)


def load_items(digest_date, json_dir=DIGEST_JSON_DIR, md_dir=DAILY_MD_DIR, track_map=None):
    """Prefer the JSON contract; fall back to the archived Markdown.

    Returns (items, source_label) so callers can report which input was used.
    """
    payload = load_digest_json(digest_date, json_dir)
    if payload:
        return items_from_digest_json(payload, track_map), "json"
    return items_from_markdown(digest_date, md_dir, track_map), "markdown"


def _item_from_md_match(match, group, subgroup):
    title = md_unescape(match.group("title")).strip()
    url = match.group("url") or ""
    if not title or url == "#":
        return None
    badge = md_unescape(match.group("badge") or "")
    source, _, section = (part.strip() for part in _split_badge(badge))
    rest = match.group("rest") or ""

    raw = {
        "title": title,
        "url": url,
        "source": source or subgroup,
        "outlet": source,
        "section": section or subgroup,
        "group": group,
    }
    points = _HN_POINTS_RE.search(rest)
    score = _SCORE_RE.search(rest)
    if points:
        raw["score"] = float(points.group("pts").replace(",", ""))
    elif score:
        raw["score"] = float(score.group("score"))
    date = _ISO_DATE_RE.search(rest)
    if date:
        raw["date"] = date.group(0)
    return raw


def _split_badge(badge):
    parts = [part for part in badge.split("·")]
    if len(parts) >= 2:
        return parts[0], "", "·".join(parts[1:])
    return (parts[0] if parts else ""), "", ""


def _group_for_heading(label):
    for needle, group in _MD_SECTION_GROUPS:
        if needle.lower() in label.lower():
            return group
    return ""


def _iso(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]
