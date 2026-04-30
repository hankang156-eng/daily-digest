#!/usr/bin/env python3
"""
Daily Digest - personal news digest generator.

Outputs:
  - digest_YYYY-MM-DD.html
  - digest_YYYY-MM-DD.md
  - index.html
  - hn_archive.md / hn_archive_data.json
  - dd_archive.md / dd_archive_data.json
"""

import datetime
import html
import json
import os
import re
import subprocess
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
CONFIG_FILE = SCRIPT_DIR / "config.json"

DEFAULT_CONFIG = {
    "settings": {
        "essential_hn_count": 5,
        "expanded_hn_count": 12,
        "essential_news_count": 5,
        "expanded_news_count": 5
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
    ("MIT Sloan Review", "https://sloanreview.mit.edu/feed/"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Shkspr.mobi", "https://shkspr.mobi/blog/feed/"),
    ("Rachel by the Bay", "https://rachelbythebay.com/w/atom.xml"),
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
    "MIT Sloan Review": "Business",
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
    "Rachel by the Bay": "Strategy",
    "Shkspr.mobi": "Strategy",
    "LinkedIn": "Business",
}

BLOG_SECURITY = {"Krebs on Security", "Troy Hunt"}
BLOG_TECH = {
    "Simon Willison", "Dan Luu", "Tonsky.me", "Paul Graham",
    "Gwern.net", "Lemire.me", "Neal.fun"
}
BLOG_STRATEGY = {
    "MIT Sloan Review", "Daring Fireball", "Rachel by the Bay", "Shkspr.mobi"
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


def _is_opinion(title, section):
    if (section or "").lower() == "opinion":
        return True
    lower = (title or "").lower()
    return any(signal in lower for signal in OPINION_SIGNALS)


def _infer_topic(article):
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


def fetch_hackernews(n=12, date=None):
    if date is None:
        date = get_yesterday()
    start, end = unix_range(date)
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?tags=story&hitsPerPage=1000"
        f"&numericFilters=created_at_i>{start},created_at_i<{end}"
    )
    response = _request_get(url, "HN", timeout=20)
    if response is None:
        return []
    try:
        hits = response.json().get("hits", [])
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
                "score": hit.get("points", 0) or 0,
                "comments": hit.get("num_comments", 0) or 0,
                "author": hit.get("author", ""),
                "source": "HackerNews",
                "category": "tech",
                "outlet": "HN",
                "section": None,
                "date": date,
            })
        return sorted(dedupe_articles(stories), key=lambda item: item["score"], reverse=True)[:n]
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
    if not posts and source == "Rachel by the Bay":
        posts = _scrape_links("https://rachelbythebay.com/w/", source, "long-form", max_items=10, min_title_len=10)
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
    hn_count = int(settings.get("essential_hn_count", 5))
    expanded_hn_count = int(settings.get("expanded_hn_count", 12))
    more_news_count = int(settings.get("expanded_news_count", 5))

    nyt_opinion, nyt_news = _split_news(data["nyt"])
    wsj_opinion, wsj_news = _split_news(data["wsj"])

    espresso_news = dedupe_articles(nyt_news[:3] + wsj_news[:2])
    used_news = {article_key(item) for item in espresso_news}
    all_news = dedupe_articles(nyt_news + wsj_news)
    lungo_news = [item for item in all_news if article_key(item) not in used_news][:more_news_count]

    all_opinion = dedupe_articles(nyt_opinion + wsj_opinion)
    espresso_opinion = all_opinion[:3]
    used_opinion = {article_key(item) for item in espresso_opinion}
    lungo_opinion = [item for item in all_opinion if article_key(item) not in used_opinion][:3]

    blogs = data["blogs"]
    return {
        "espresso_hn": data["hn"][:hn_count],
        "lungo_hn": data["hn"][hn_count:expanded_hn_count],
        "espresso_news": espresso_news,
        "lungo_news": lungo_news,
        "espresso_opinion": espresso_opinion,
        "lungo_opinion": lungo_opinion,
        "mit": data["mit"],
        "linkedin": data["linkedin"],
        "security": [item for item in blogs if item.get("source") in BLOG_SECURITY],
        "tech": [item for item in blogs if item.get("source") in BLOG_TECH],
        "strategy": [item for item in blogs if item.get("source") in BLOG_STRATEGY],
    }


def _display_date(article):
    pub = article.get("date")
    if isinstance(pub, datetime.date):
        label = pub.isoformat()
    elif pub:
        label = str(pub)
    else:
        return ""
    return f"latest from {label}" if article.get("is_fallback") else label


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
    now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
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
    if show_score:
        score = (
            f' <span style="font-size:11px;color:#888;margin-left:6px">'
            f'{int(article.get("score", 0))} pts · {int(article.get("comments", 0))} comments</span>'
        )
    discuss = ""
    if article.get("hn_url"):
        discuss = (
            f' <a href="{_html_escape(article["hn_url"])}" style="font-size:11px;color:#e67e22;'
            f'margin-left:6px;text-decoration:none">discuss</a>'
        )
    date_note = _display_date(article)
    if date_note:
        date_note = f' <span style="font-size:11px;color:#999;margin-left:6px">{_html_escape(date_note)}</span>'

    return f'''<tr>
      <td style="padding:9px 0;border-bottom:1px solid #f5f5f5;vertical-align:top">
        <a href="{_html_escape(article.get("url", "#"))}" style="color:#1a1a2e;font-size:14px;text-decoration:none;line-height:1.45;font-weight:500" target="_blank">{_html_escape(article.get("title", ""))}</a>
        {badges}{score}{discuss}{date_note}
      </td>
    </tr>'''


def _section_block(heading, articles, show_score=False):
    if not articles:
        return ""
    rows = "".join(_article_row(article, show_score=show_score) for article in articles)
    return f'''
    <div style="margin-bottom:28px">
      <h3 style="font-size:13px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px;padding-bottom:8px;border-bottom:2px solid #f0f0f0">{heading}</h3>
      <table width="100%" cellspacing="0" cellpadding="0"><tbody>{rows}</tbody></table>
    </div>'''


def generate_html(date, data, settings=None):
    settings = settings or DEFAULT_CONFIG["settings"]
    sections = build_sections(data, settings)
    date_str = date.strftime("%A, %B %-d, %Y")

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
  <div style="font-size:14px;color:rgba(255,255,255,.5);margin-top:6px">Daily Digest</div>
</td></tr>
<tr><td style="height:16px"></td></tr>
<tr><td style="background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:800;color:#e74c3c;letter-spacing:.15em;text-transform:uppercase;margin-bottom:22px;padding-bottom:14px;border-bottom:1px solid #f0f0f0">☕️ Espresso</div>
  {_section_block("🔶 HackerNews Top 5", sections["espresso_hn"], show_score=True)}
  {_section_block("📰 Current Events & News", sections["espresso_news"])}
  {_section_block("💭 Opinion & Analysis", sections["espresso_opinion"])}
  {_section_block("🎓 MIT Research & Insights", sections["mit"])}
  {_section_block("💼 LinkedIn - Rama's Activity", sections["linkedin"])}
</td></tr>
<tr><td style="height:16px"></td></tr>
<tr><td style="background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:800;color:#3498db;letter-spacing:.15em;text-transform:uppercase;margin-bottom:22px;padding-bottom:14px;border-bottom:1px solid #f0f0f0">📚 Lungo</div>
  {_section_block("🔶 HackerNews #6-#12", sections["lungo_hn"], show_score=True)}
  {_section_block("📰 More Headlines", sections["lungo_news"])}
  {_section_block("💭 More Opinion", sections["lungo_opinion"])}
  {_section_block("🔒 Security & Privacy", sections["security"])}
  {_section_block("⚙️ Tech & Engineering", sections["tech"])}
  {_section_block("🧠 Strategy & Craft", sections["strategy"])}
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
        section = article.get("section", "")
        badge = f"**[{_md_escape(source)}" + (f" · {_md_escape(section)}" if section else "") + "]** " if source else ""
        score = ""
        if show_score:
            score = f" {int(article.get('score', 0))} pts · {int(article.get('comments', 0))} comments"
        discuss = f" · [discuss]({article['hn_url']})" if article.get("hn_url") else ""
        date_note = f" · {_md_escape(_display_date(article))}" if _display_date(article) else ""
        prefix = f"{index}." if numbered else "-"
        lines.append(f"{prefix} {badge}[{title}]({url}){score}{discuss}{date_note}")
    return "\n".join(lines)


def generate_markdown(date, data, settings=None):
    settings = settings or DEFAULT_CONFIG["settings"]
    sections = build_sections(data, settings)
    date_str = date.strftime("%A, %B %-d, %Y")

    parts = [f"# Daily Digest - {date_str}", "", "---", "", "## ☕️ Espresso", ""]

    def sec(heading, articles, numbered=False, score=False):
        if not articles:
            return []
        return [f"### {heading}", "", _md_articles(articles, numbered=numbered, show_score=score), ""]

    parts += sec("🔶 HackerNews Top 5", sections["espresso_hn"], numbered=True, score=True)
    parts += sec("📰 Current Events & News", sections["espresso_news"])
    parts += sec("💭 Opinion & Analysis", sections["espresso_opinion"])
    parts += sec("🎓 MIT Research & Insights", sections["mit"])
    parts += sec("💼 LinkedIn - Rama's Activity", sections["linkedin"])
    parts += ["---", "", "## 📚 Lungo", ""]
    parts += sec("🔶 HackerNews #6-#12", sections["lungo_hn"], numbered=True, score=True)
    parts += sec("📰 More Headlines", sections["lungo_news"])
    parts += sec("💭 More Opinion", sections["lungo_opinion"])
    parts += sec("🔒 Security & Privacy", sections["security"])
    parts += sec("⚙️ Tech & Engineering", sections["tech"])
    parts += sec("🧠 Strategy & Craft", sections["strategy"])
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

    json_path = SCRIPT_DIR / "hn_archive_data.json"
    md_path = SCRIPT_DIR / "hn_archive.md"
    xlsx_path = SCRIPT_DIR / "hn_archive.xlsx"
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
        "espresso_hn", "lungo_hn", "espresso_news", "lungo_news",
        "espresso_opinion", "lungo_opinion", "mit", "linkedin",
        "security", "tech", "strategy"
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
    json_path = SCRIPT_DIR / "dd_archive_data.json"
    md_path = SCRIPT_DIR / "dd_archive.md"
    xlsx_path = SCRIPT_DIR / "dd_archive.xlsx"
    archive = {}
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as f:
                archive = json.load(f)
        except Exception as e:
            print(f"  [DD Archive] Error reading JSON: {e}; rebuilding from current run.")
            archive = {}

    date_key = date.isoformat()
    action = "Replaced" if date_key in archive else "Added"
    archive[date_key] = _flatten_digest(date, data, settings=settings)
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2, default=str)
        print(f"  [DD Archive] {action} {len(archive[date_key])} items for {date_key}")
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


def _run_git(args):
    result = subprocess.run(
        ["git", "-C", str(SCRIPT_DIR)] + args,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown git error").strip()
        print(f"  [GitHub] git {' '.join(args)} failed: {message}")
        return False, result
    return True, result


def push_to_github(date, config):
    if os.environ.get("DAILY_DIGEST_SKIP_GITHUB") == "1":
        print("  [GitHub] Skipped because DAILY_DIGEST_SKIP_GITHUB=1.")
        return
    if not config.get("github_pages", {}).get("enabled"):
        return
    try:
        files = [
            f"digest_{date.isoformat()}.html",
            f"digest_{date.isoformat()}.md",
            "index.html",
            "hn_archive.md",
            "hn_archive.xlsx",
            "dd_archive.md",
            "dd_archive.xlsx",
        ]
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


def main(target_date=None):
    print(f"\n{'-' * 50}")
    print(f"  Daily Digest - {datetime.date.today()}")
    print(f"{'-' * 50}")

    config = load_config()
    settings = config.get("settings", {})
    date = target_date or get_yesterday()
    print(f"  Fetching content for: {date}\n")

    print("  [1/6] HackerNews...")
    hn = fetch_hackernews(n=int(settings.get("expanded_hn_count", 12)), date=date)

    print("  [2/6] NYT...")
    nyt = fetch_news(NYT_FEEDS)

    print("  [3/6] WSJ...")
    wsj = fetch_news(WSJ_FEEDS)

    print("  [4/6] MIT sites...")
    mit = fetch_mit_updates(since_date=date)

    print("  [5/6] Blogs...")
    blogs = fetch_blog_updates(since_date=date)

    print("  [6/6] LinkedIn...")
    linkedin = fetch_linkedin_activity()

    data = {
        "hn": hn,
        "nyt": nyt,
        "wsj": wsj,
        "mit": mit,
        "blogs": blogs,
        "linkedin": linkedin,
    }

    print("\n  Generating outputs...")
    html_doc = generate_html(date, data, settings)
    markdown = generate_markdown(date, data, settings)

    html_path = SCRIPT_DIR / f"digest_{date.isoformat()}.html"
    md_path = SCRIPT_DIR / f"digest_{date.isoformat()}.md"
    index_path = SCRIPT_DIR / "index.html"

    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"  Saved HTML:     {html_path.name}")
        print("  Saved index.html")
        print(f"  Saved Markdown: {md_path.name}")
    except Exception as e:
        print(f"  [Output] Error writing digest files: {e}")

    print("  Updating HN archive...")
    update_hn_archive(date, hn)

    print("  Updating digest archive...")
    update_dd_archive(date, data, settings=settings)

    print("  GitHub Pages...")
    push_to_github(date, config)

    print("\n  Done!\n")
    return {
        "html_path": str(html_path),
        "md_path": str(md_path),
        "html": html_doc,
        "md": markdown,
        "date": date.isoformat(),
    }


if __name__ == "__main__":
    main()
