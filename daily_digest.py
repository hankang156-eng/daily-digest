#!/usr/bin/env python3
"""
Daily Digest — Personal news aggregator for Michelle
Fetches: HackerNews, NYT, WSJ, MIT sites, curated blogs
Outputs each morning:
  - digest_YYYY-MM-DD.html  (styled HTML)
  - digest_YYYY-MM-DD.md    (clean markdown)
  - index.html              (GitHub Pages — always = latest digest)
  - hn_archive.md           (running HN top-10 table, appended daily)
  - hn_archive_data.json    (raw HN archive data)
  - dd_archive.md           (running full-digest table, appended daily)
  - dd_archive_data.json    (raw digest archive data)
  Optionally updates a Google Sheet and pushes to GitHub Pages.

Usage: python3 daily_digest.py
Config: config.json
"""

import json
import datetime
import time
import subprocess
import sys
import re
from pathlib import Path

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
    print("FATAL: pip3 install requests --break-system-packages")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.json"

# ─── Default Config ───────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "settings": {
        "essential_hn_count": 6,
        "expanded_hn_count": 10,
        "essential_news_count": 6,
        "expanded_news_count": 10
    },
    "github_pages": {
        "enabled": False,
        "_setup": "Run: bash setup_github_pages.sh"
    },
    "google_sheets": {
        "enabled": False,
        "sheet_id": "YOUR_GOOGLE_SHEET_ID_HERE",
        "credentials_file": "google_credentials.json",
        "_setup": "See README.md — Google Sheets Setup section"
    }
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    with open(CONFIG_FILE, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    return DEFAULT_CONFIG

# ─── Date Helpers ─────────────────────────────────────────────────────────────

def get_yesterday():
    return datetime.date.today() - datetime.timedelta(days=1)

def unix_range(date):
    start = datetime.datetime.combine(date, datetime.time.min)
    end   = datetime.datetime.combine(date, datetime.time.max)
    return int(start.timestamp()), int(end.timestamp())

# ─── Topic Inference ──────────────────────────────────────────────────────────

_SECTION_TOPICS = {
    "U.S.": "U.S. News", "Business": "Business",
    "Opinion": "Opinion", "Lifestyle": "Lifestyle",
}
_SOURCE_TOPICS = {
    "Krebs on Security": "Security",  "Troy Hunt": "Security",
    "MIT IDE": "Research",            "MIT Shaping Work": "Research",
    "MIT Sloan Review": "Research",
    "Simon Willison": "Technology",   "Dan Luu": "Technology",
    "Tonsky.me": "Technology",        "Paul Graham": "Technology",
    "Gwern.net": "Technology",        "Lemire.me": "Technology",
    "Neal.fun": "Technology",
    "Stratechery": "Strategy",        "Daring Fireball": "Strategy",
    "Rachel by the Bay": "Engineering","Shkspr.mobi": "Craft",
    "LinkedIn": "Professional",
}

def _infer_topic(a):
    if a.get("outlet") == "HN":
        return "Technology"
    section = a.get("section", "")
    if section and section in _SECTION_TOPICS:
        return _SECTION_TOPICS[section]
    source = a.get("source", "")
    if source in _SOURCE_TOPICS:
        return _SOURCE_TOPICS[source]
    cat = a.get("category", "")
    return {"news": "News", "opinion": "Opinion",
            "long-form": "Long-form", "research": "Research",
            "tech": "Technology"}.get(cat, "General")

# ─── HackerNews ───────────────────────────────────────────────────────────────

def fetch_hackernews(n=10, date=None):
    if date is None:
        date = get_yesterday()
    start, end = unix_range(date)
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?tags=front_page&hitsPerPage=50"
        f"&numericFilters=created_at_i>{start},created_at_i<{end}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        stories = []
        for h in hits:
            stories.append({
                "title":    h.get("title", "").strip(),
                "url":      h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "hn_url":   f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "score":    h.get("points", 0) or 0,
                "comments": h.get("num_comments", 0) or 0,
                "author":   h.get("author", ""),
                "source":   "HackerNews",
                "category": "tech",
                "outlet":   "HN",
                "section":  None,
            })
        return sorted(stories, key=lambda x: x["score"], reverse=True)[:n]
    except Exception as e:
        print(f"  [HN] Error: {e}")
        return []

# ─── RSS ──────────────────────────────────────────────────────────────────────

def _rss_date(entry):
    for attr in ("published_parsed", "updated_parsed"):
        v = getattr(entry, attr, None)
        if v:
            try:
                return datetime.date(v.tm_year, v.tm_mon, v.tm_mday)
            except Exception:
                pass
    return None

def fetch_rss(url, source, max_items=30, since_date=None):
    if not HAS_FEEDPARSER:
        return []
    try:
        feed = feedparser.parse(url)
        out = []
        for entry in feed.entries[:max_items]:
            pub = _rss_date(entry)
            if since_date and pub and pub < since_date:
                continue
            title = (entry.get("title") or "").strip()
            link  = entry.get("link", "")
            if not title or not link:
                continue
            out.append({
                "title":    title,
                "url":      link,
                "date":     pub,
                "source":   source,
                "summary":  re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:180].strip(),
                "category": "news",
                "outlet":   None,
                "section":  None,
            })
        return out
    except Exception as e:
        print(f"  [RSS:{source}] Error: {e}")
        return []

# ─── NYT & WSJ ────────────────────────────────────────────────────────────────

NYT_FEEDS = [
    ("U.S.",      "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    ("Business",  "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Opinion",   "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/Opinion.xml"),
    ("Lifestyle", "NYT", "https://rss.nytimes.com/services/xml/rss/nyt/FashionandStyle.xml"),
]
WSJ_FEEDS = [
    ("U.S.",      "WSJ", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("Business",  "WSJ", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("Opinion",   "WSJ", "https://feeds.a.dj.com/rss/RSSOpinion.xml"),
    ("Lifestyle", "WSJ", "https://feeds.a.dj.com/rss/RSSWSJ.xml"),
]

OPINION_KW = {"opinion","editorial","column","commentary","perspective",
              "essay","review","the case for","the case against"}

def _categorize(title, section):
    if section.lower() == "opinion":
        return "opinion"
    if any(kw in title.lower() for kw in OPINION_KW):
        return "opinion"
    return "news"

def fetch_news(feeds, n_per_feed=8):
    articles = []
    for section, outlet, url in feeds:
        items = fetch_rss(url, f"{outlet} {section}", max_items=n_per_feed)
        for a in items:
            a["outlet"]   = outlet
            a["section"]  = section
            a["category"] = _categorize(a["title"], section)
        articles.extend(items)
    return articles

# ─── MIT Sites ────────────────────────────────────────────────────────────────

MIT_RSS = {
    "MIT IDE":          ["https://ide.mit.edu/feed/"],
    "MIT Shaping Work": ["https://shapingwork.mit.edu/feed/"],
}
MIT_SCRAPE = {
    "MIT IDE":          "https://ide.mit.edu/latest-insights/",
    "MIT Shaping Work": "https://shapingwork.mit.edu/research/",
}

def _scrape_links(url, source):
    if not HAS_BS4:
        return []
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        base = "https://" + url.split("/")[2]
        out = []
        for tag in soup.find_all("a", href=True):
            title = tag.get_text(strip=True)
            href  = tag["href"]
            if len(title) < 25:
                continue
            if not href.startswith("http"):
                href = base + "/" + href.lstrip("/")
            out.append({"title": title, "url": href, "source": source,
                        "category": "research", "date": None,
                        "outlet": None, "section": None})
        return out[:5]
    except Exception as e:
        print(f"  [Scrape:{source}] Error: {e}")
        return []

def fetch_mit_updates(since_date=None):
    articles = []
    for source, rss_urls in MIT_RSS.items():
        found = []
        for rss_url in rss_urls:
            items = fetch_rss(rss_url, source, max_items=10, since_date=since_date)
            if items:
                found = items
                break
        if not found:
            found = _scrape_links(MIT_SCRAPE[source], source)
        for a in found:
            a["category"] = "research"
        articles.extend(found[:4])
    return articles

# ─── LinkedIn ─────────────────────────────────────────────────────────────────

def fetch_linkedin_activity():
    return [{
        "title":    "View Ramar's recent LinkedIn activity →",
        "url":      "https://www.linkedin.com/in/ramar/recent-activity/all/",
        "source":   "LinkedIn",
        "category": "social",
        "date":     None,
        "outlet":   None,
        "section":  None,
    }]

# ─── Blogs ────────────────────────────────────────────────────────────────────

BLOG_FEEDS = [
    ("MIT Sloan Review",  "https://sloanreview.mit.edu/feed/"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    ("Simon Willison",    "https://simonwillison.net/atom/everything/"),
    ("Shkspr.mobi",       "https://shkspr.mobi/blog/feed/"),
    ("Rachel by the Bay", "https://rachelbythebay.com/w/atom.xml"),
    ("Dan Luu",           "https://danluu.com/atom.xml"),
    ("Daring Fireball",   "https://daringfireball.net/feeds/main"),
    ("Tonsky.me",         "https://tonsky.me/blog/atom.xml"),
    ("Troy Hunt",         "https://feeds.feedburner.com/TroyHunt"),
    ("Lemire.me",         "https://lemire.me/blog/feed/"),
    ("Gwern.net",         "https://gwern.net/feed/daily"),
]

def _scrape_paulgraham():
    try:
        r = requests.get("http://paulgraham.com/articles.html", timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        if not HAS_BS4:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href  = a["href"]
            if len(title) > 10:
                if not href.startswith("http"):
                    href = "http://paulgraham.com/" + href
                links.append({"title": title, "url": href, "source": "Paul Graham",
                               "category": "long-form", "date": None,
                               "outlet": None, "section": None})
        return links[:2]
    except Exception:
        return []

def fetch_blog_updates(since_date=None):
    if since_date is None:
        since_date = get_yesterday()
    all_posts = []
    for name, url in BLOG_FEEDS:
        posts = fetch_rss(url, name, max_items=20, since_date=since_date)
        if not posts:
            posts = fetch_rss(url, name, max_items=3)
        for p in posts:
            p["category"] = "long-form"
        all_posts.extend(posts[:2])
    all_posts.extend(_scrape_paulgraham()[:1])
    all_posts.append({
        "title": "Stratechery — latest post", "url": "https://stratechery.com",
        "source": "Stratechery", "category": "long-form",
        "date": None, "outlet": None, "section": None,
    })
    return all_posts

# ─── HTML Generation ──────────────────────────────────────────────────────────

_OUTLET_COLORS = {
    "NYT": ("#111", "#fff"),
    "WSJ": ("#00285e", "#fff"),
    "HN":  ("#ff6600", "#fff"),
}

def _badge(text, bg="#eee", color="#333"):
    return (f'<span style="display:inline-block;background:{bg};color:{color};font-size:10px;'
            f'font-weight:700;padding:2px 6px;border-radius:4px;letter-spacing:.04em;'
            f'margin-left:4px;vertical-align:middle">{text}</span>')

def _article_row(a, show_score=False):
    badges = ""
    if a.get("outlet") and a["outlet"] in _OUTLET_COLORS:
        bg, fg = _OUTLET_COLORS[a["outlet"]]
        badges += _badge(a["outlet"], bg, fg)
    if a.get("section"):
        badges += _badge(a["section"], "#f0f0f0", "#666")
    if a.get("source") and not a.get("outlet"):
        badges += _badge(a["source"], "#f5f5f5", "#888")
    score_str = ""
    if show_score and a.get("score"):
        score_str = (f' <span style="font-size:11px;color:#aaa;margin-left:6px">'
                     f'▲{a["score"]} · 💬{a.get("comments",0)}</span>')
    hn_link = ""
    if a.get("hn_url"):
        hn_link = (f' <a href="{a["hn_url"]}" style="font-size:11px;color:#e67e22;'
                   f'margin-left:6px;text-decoration:none">discuss</a>')
    return f'''<tr>
      <td style="padding:9px 0;border-bottom:1px solid #f5f5f5;vertical-align:top">
        <a href="{a.get("url","#")}" style="color:#1a1a2e;font-size:14px;text-decoration:none;
           line-height:1.45;font-weight:500" target="_blank">{a.get("title","")}</a>
        {badges}{score_str}{hn_link}
      </td>
    </tr>'''

def _section_block(heading, articles, show_score=False, icon=""):
    if not articles:
        return ""
    rows = "".join(_article_row(a, show_score) for a in articles)
    return f'''
    <div style="margin-bottom:28px">
      <h3 style="font-size:13px;font-weight:700;color:#555;text-transform:uppercase;
                 letter-spacing:.08em;margin:0 0 10px;padding-bottom:8px;
                 border-bottom:2px solid #f0f0f0">{icon} {heading}</h3>
      <table width="100%" cellspacing="0" cellpadding="0"><tbody>{rows}</tbody></table>
    </div>'''

def generate_html(date, data):
    date_str = date.strftime("%A, %B %-d, %Y")

    def split_cat(arts):
        op   = [a for a in arts if a.get("category") == "opinion"]
        news = [a for a in arts if a.get("category") != "opinion"]
        return op, news

    nyt_op, nyt_news = split_cat(data["nyt"])
    wsj_op, wsj_news = split_cat(data["wsj"])
    ess_news    = (nyt_news[:3] + wsj_news[:3])[:6]
    ess_opinion = (nyt_op[:2]  + wsj_op[:2])[:6]
    exp_hn      = data["hn_expanded"][6:]
    all_news    = data["nyt"] + data["wsj"]
    exp_news    = [a for a in all_news if a.get("category") != "opinion"][6:10]
    exp_op      = [a for a in all_news if a.get("category") == "opinion"][4:10]
    blog_sec    = [b for b in data["blogs"] if b.get("source") in ("Krebs on Security","Troy Hunt")]
    blog_tech   = [b for b in data["blogs"] if b.get("source") in
                   ("Simon Willison","Dan Luu","Tonsky.me","Paul Graham","Neal.fun","Gwern.net","Lemire.me")]
    blog_strat  = [b for b in data["blogs"] if b.get("source") in
                   ("MIT Sloan Review","Stratechery","Daring Fireball","Rachel by the Bay","Shkspr.mobi")]

    def sb(h, arts, score=False, icon=""):
        return _section_block(h, arts, show_score=score, icon=icon)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Daily Digest — {date_str}</title>
</head>
<body style="margin:0;padding:0;background:#f4f3f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Georgia,serif;color:#1a1a1a">
<table width="100%" cellspacing="0" cellpadding="0" style="background:#f4f3f0">
<tr><td align="center" style="padding:24px 16px">
<table width="660" cellspacing="0" cellpadding="0" style="max-width:660px;width:100%">

<tr><td style="background:#1a1a2e;border-radius:12px;padding:32px 36px">
  <div style="font-size:11px;color:rgba(255,255,255,.5);letter-spacing:.15em;text-transform:uppercase;margin-bottom:6px">Your Morning Read</div>
  <div style="font-size:30px;font-weight:800;color:#fff;line-height:1.1">{date_str}</div>
  <div style="font-size:14px;color:rgba(255,255,255,.45);margin-top:6px">Daily Digest</div>
</td></tr>
<tr><td style="height:16px"></td></tr>

<tr><td style="background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:800;color:#e74c3c;letter-spacing:.15em;text-transform:uppercase;margin-bottom:22px;padding-bottom:14px;border-bottom:1px solid #f0f0f0">⭐ Essential</div>
  {sb("HackerNews Top Stories",      data["hn_essential"], score=True, icon="🔶")}
  {sb("Current Events & News",       ess_news,                         icon="📰")}
  {sb("Opinion & Analysis",          ess_opinion,                      icon="💭")}
  {sb("MIT Research & Insights",     data["mit"],                      icon="🎓")}
  {sb("LinkedIn — Ramar's Activity", data["linkedin"],                 icon="💼")}
</td></tr>
<tr><td style="height:16px"></td></tr>

<tr><td style="background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:800;color:#3498db;letter-spacing:.15em;text-transform:uppercase;margin-bottom:22px;padding-bottom:14px;border-bottom:1px solid #f0f0f0">📚 Expanded</div>
  {sb("HackerNews #7–#10",  exp_hn,    score=True, icon="🔶")}
  {sb("More Headlines",     exp_news,              icon="📰")}
  {sb("More Opinion",       exp_op,                icon="💭")}
  {sb("Security & Privacy", blog_sec,              icon="🔒")}
  {sb("Tech & Engineering", blog_tech,             icon="⚙️")}
  {sb("Strategy & Craft",   blog_strat,            icon="🧠")}
</td></tr>
<tr><td style="height:16px"></td></tr>

<tr><td style="text-align:center;padding:16px;font-size:11px;color:#bbb">
  Generated {datetime.datetime.now().strftime("%-I:%M %p on %B %-d, %Y")} · Daily Digest
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
    return html

# ─── Markdown Generation ──────────────────────────────────────────────────────

def _md_articles(articles, numbered=False, show_score=False):
    lines = []
    for i, a in enumerate(articles, 1):
        title   = a.get("title", "").replace("[","\\[").replace("]","\\]")
        url     = a.get("url", "#")
        source  = a.get("outlet") or a.get("source", "")
        section = a.get("section", "")
        badge   = f"**[{source}" + (f" · {section}" if section else "") + "]** " if source else ""
        score_s = f" ▲{a['score']} · 💬{a.get('comments',0)}" if show_score and a.get("score") else ""
        discuss = f" · [discuss]({a['hn_url']})" if a.get("hn_url") else ""
        prefix  = f"{i}." if numbered else "-"
        lines.append(f"{prefix} {badge}[{title}]({url}){score_s}{discuss}")
    return "\n".join(lines)

def generate_markdown(date, data):
    date_str = date.strftime("%A, %B %-d, %Y")

    def split_cat(arts):
        return ([a for a in arts if a.get("category") == "opinion"],
                [a for a in arts if a.get("category") != "opinion"])

    nyt_op, nyt_news = split_cat(data["nyt"])
    wsj_op, wsj_news = split_cat(data["wsj"])
    ess_news    = (nyt_news[:3] + wsj_news[:3])[:6]
    ess_opinion = (nyt_op[:2]  + wsj_op[:2])[:6]
    exp_hn      = data["hn_expanded"][6:]
    all_news    = data["nyt"] + data["wsj"]
    exp_news    = [a for a in all_news if a.get("category") != "opinion"][6:10]
    exp_op      = [a for a in all_news if a.get("category") == "opinion"][4:10]
    blog_sec    = [b for b in data["blogs"] if b.get("source") in ("Krebs on Security","Troy Hunt")]
    blog_tech   = [b for b in data["blogs"] if b.get("source") in
                   ("Simon Willison","Dan Luu","Tonsky.me","Paul Graham","Neal.fun","Gwern.net","Lemire.me")]
    blog_strat  = [b for b in data["blogs"] if b.get("source") in
                   ("MIT Sloan Review","Stratechery","Daring Fireball","Rachel by the Bay","Shkspr.mobi")]

    parts = [f"# Daily Digest — {date_str}", "", "---", "", "## ⭐ Essential", ""]

    def sec(heading, arts, numbered=False, score=False):
        if not arts:
            return []
        return [f"### {heading}", "", _md_articles(arts, numbered=numbered, show_score=score), ""]

    parts += sec("🔶 HackerNews Top Stories",      data["hn_essential"], numbered=True, score=True)
    parts += sec("📰 Current Events & News",        ess_news)
    parts += sec("💭 Opinion & Analysis",            ess_opinion)
    parts += sec("🎓 MIT Research & Insights",       data["mit"])
    parts += sec("💼 LinkedIn — Ramar's Activity",   data["linkedin"])
    parts += ["---", "", "## 📚 Expanded", ""]
    parts += sec(f"🔶 HackerNews #7–#{6+len(exp_hn)}", exp_hn, numbered=True, score=True)
    parts += sec("📰 More Headlines",                exp_news)
    parts += sec("💭 More Opinion",                  exp_op)
    parts += sec("🔒 Security & Privacy",            blog_sec)
    parts += sec("⚙️ Tech & Engineering",            blog_tech)
    parts += sec("🧠 Strategy & Craft",              blog_strat)
    parts += ["---",
              f"*Generated {datetime.datetime.now().strftime('%-I:%M %p')} "
              f"on {datetime.date.today().strftime('%B %-d, %Y')}*"]
    return "\n".join(parts)

# ─── HN Archive (JSON + MD table) ─────────────────────────────────────────────

def _hn_md_table(archive):
    """Generate full hn_archive.md as a markdown table, newest date first."""
    lines = [
        "# HackerNews Daily Top 10 — Archive",
        "",
        f"*Last updated: {datetime.date.today().isoformat()} · "
        f"{len(archive)} days · "
        f"{sum(len(v) for v in archive.values())} stories*",
        "",
        "| Date | Day | Rank | Title | Points | Comments | Topic | URL |",
        "|------|-----|------|-------|--------|----------|-------|-----|",
    ]
    for date_str in sorted(archive.keys(), reverse=True):
        day = datetime.date.fromisoformat(date_str).strftime("%A")
        for rank, s in enumerate(archive[date_str], 1):
            title   = s["title"].replace("|", "\\|")
            topic   = _infer_topic(s)
            url     = s.get("url", "")
            hn_url  = s.get("hn_url", "")
            link    = f"[link]({url})" if url else ""
            hn_link = f"[HN]({hn_url})" if hn_url else ""
            links   = " · ".join(filter(None, [link, hn_link]))
            lines.append(
                f"| {date_str} | {day} | {rank} | {title} "
                f"| {s.get('score',0)} | {s.get('comments',0)} "
                f"| {topic} | {links} |"
            )
    return "\n".join(lines)

def update_hn_archive(date, new_stories):
    """Append today's HN top-10 to JSON and regenerate hn_archive.md."""
    if not new_stories:
        print("  [HN Archive] No stories — skipping.")
        return

    json_path = SCRIPT_DIR / "hn_archive_data.json"
    md_path   = SCRIPT_DIR / "hn_archive.md"

    archive = {}
    if json_path.exists():
        try:
            with open(json_path) as f:
                archive = json.load(f)
        except Exception:
            archive = {}

    date_key = date.isoformat()
    if date_key not in archive:
        archive[date_key] = new_stories
        with open(json_path, "w") as f:
            json.dump(archive, f, ensure_ascii=False)
        print(f"  [HN Archive] Added {len(new_stories)} stories for {date_key} ({len(archive)} days total)")
    else:
        print(f"  [HN Archive] {date_key} already present — skipping JSON update")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_hn_md_table(archive))
    print(f"  [HN Archive] Regenerated hn_archive.md")

# ─── Daily Digest Archive (JSON + MD table + Google Sheet) ────────────────────

def _flatten_digest(date, data):
    """Flatten all articles in a digest data dict into a list of records."""
    records = []
    digest_date = date.isoformat()

    def add(arts):
        for a in arts:
            if not a.get("title") or not a.get("url"):
                continue
            pub = a.get("date")
            pub_str = pub.isoformat() if isinstance(pub, datetime.date) else (pub or "")
            records.append({
                "digest_date": digest_date,
                "title":       a.get("title", "").strip(),
                "source":      (a.get("outlet") or a.get("source", "")).strip(),
                "section":     (a.get("section") or "").strip(),
                "topic":       _infer_topic(a),
                "category":    a.get("category", ""),
                "pub_date":    pub_str,
                "url":         a.get("url", ""),
                "hn_url":      a.get("hn_url", ""),
            })

    add(data.get("hn_expanded",  []))
    add(data.get("nyt",          []))
    add(data.get("wsj",          []))
    add(data.get("mit",          []))
    add(data.get("blogs",        []))
    add(data.get("linkedin",     []))
    return records

def _dd_md_table(archive):
    """Generate dd_archive.md as a markdown table, newest date first."""
    lines = [
        "# Daily Digest — Archive",
        "",
        f"*Last updated: {datetime.date.today().isoformat()} · "
        f"{len(archive)} digest days*",
        "",
        "| Digest Date | Title | Source | Topic | Category | Pub Date | URL |",
        "|-------------|-------|--------|-------|----------|----------|-----|",
    ]
    for date_str in sorted(archive.keys(), reverse=True):
        for r in archive[date_str]:
            title   = r["title"].replace("|", "\\|")[:100]
            source  = r["source"] + (f" · {r['section']}" if r.get("section") else "")
            url     = r.get("url", "")
            link    = f"[link]({url})" if url else ""
            lines.append(
                f"| {r['digest_date']} | {title} | {source} "
                f"| {r['topic']} | {r['category']} | {r['pub_date']} | {link} |"
            )
    return "\n".join(lines)

def update_dd_archive(date, data, config):
    """Append today's digest to JSON, regenerate dd_archive.md, update Google Sheet."""
    json_path = SCRIPT_DIR / "dd_archive_data.json"
    md_path   = SCRIPT_DIR / "dd_archive.md"

    archive = {}
    if json_path.exists():
        try:
            with open(json_path) as f:
                archive = json.load(f)
        except Exception:
            archive = {}

    date_key = date.isoformat()
    if date_key not in archive:
        records = _flatten_digest(date, data)
        archive[date_key] = records
        with open(json_path, "w") as f:
            json.dump(archive, f, ensure_ascii=False)
        print(f"  [DD Archive] Added {len(records)} items for {date_key}")
    else:
        print(f"  [DD Archive] {date_key} already present — skipping JSON update")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_dd_md_table(archive))
    print(f"  [DD Archive] Regenerated dd_archive.md")

    # Google Sheets
    gs_config = config.get("google_sheets", {})
    if gs_config.get("enabled") and date_key in archive:
        _update_google_sheet(archive[date_key], gs_config)

def _update_google_sheet(records, gs_config):
    """Append new rows to the Google Sheet."""
    if not HAS_GSPREAD:
        print("  [Sheets] gspread not installed — run: pip3 install gspread google-auth --break-system-packages")
        return
    try:
        creds_path = SCRIPT_DIR / gs_config.get("credentials_file", "google_credentials.json")
        sheet_id   = gs_config.get("sheet_id", "")
        if not creds_path.exists() or not sheet_id or "YOUR_" in sheet_id:
            print("  [Sheets] Not configured — see README for Google Sheets setup.")
            return

        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds  = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(sheet_id).sheet1

        # Ensure header row
        existing = sheet.get_all_values()
        header = ["Digest Date", "Title", "Source", "Topic", "Category", "Pub Date", "URL"]
        if not existing:
            sheet.append_row(header)

        # Append rows for this digest
        rows = [
            [r["digest_date"], r["title"], r["source"] + (f" · {r['section']}" if r.get("section") else ""),
             r["topic"], r["category"], r["pub_date"], r["url"]]
            for r in records
        ]
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"  [Sheets] Appended {len(rows)} rows to Google Sheet")
    except Exception as e:
        print(f"  [Sheets] Error: {e}")

# ─── GitHub Pages ─────────────────────────────────────────────────────────────

def push_to_github(date, config):
    """Commit updated files and push to GitHub Pages."""
    gh = config.get("github_pages", {})
    if not gh.get("enabled"):
        return
    try:
        repo = str(SCRIPT_DIR)
        files = [
            f"digest_{date.isoformat()}.html",
            f"digest_{date.isoformat()}.md",
            "index.html",
            "hn_archive.md",
            "dd_archive.md",
        ]
        subprocess.run(["git", "-C", repo, "add"] + files,
                       capture_output=True, timeout=20)
        result = subprocess.run(
            ["git", "-C", repo, "commit", "-m", f"digest: {date.isoformat()}"],
            capture_output=True, text=True, timeout=20)
        if "nothing to commit" in result.stdout:
            print("  [GitHub] Nothing new to commit.")
            return
        subprocess.run(["git", "-C", repo, "push", "origin", "main"],
                       capture_output=True, timeout=30)
        print(f"  [GitHub] Pushed to GitHub Pages ✓")
    except Exception as e:
        print(f"  [GitHub] Error: {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main(target_date=None):
    print(f"\n{'─'*50}")
    print(f"  Daily Digest — {datetime.date.today()}")
    print(f"{'─'*50}")

    config = load_config()
    s      = config.get("settings", {})
    date   = target_date or get_yesterday()
    print(f"  Fetching content for: {date}\n")

    print("  [1/6] HackerNews…")
    hn_all       = fetch_hackernews(n=s.get("expanded_hn_count", 10), date=date)
    hn_essential = hn_all[:s.get("essential_hn_count", 6)]

    print("  [2/6] NYT…")
    nyt = fetch_news(NYT_FEEDS, n_per_feed=8)

    print("  [3/6] WSJ…")
    wsj = fetch_news(WSJ_FEEDS, n_per_feed=8)

    print("  [4/6] MIT sites…")
    mit = fetch_mit_updates(since_date=date)

    print("  [5/6] Blogs…")
    blogs = fetch_blog_updates(since_date=date)

    print("  [6/6] LinkedIn…")
    linkedin = fetch_linkedin_activity()

    data = {
        "hn_essential": hn_essential,
        "hn_expanded":  hn_all,
        "nyt":          nyt,
        "wsj":          wsj,
        "mit":          mit,
        "blogs":        blogs,
        "linkedin":     linkedin,
    }

    print("\n  Generating outputs…")

    # HTML
    html = generate_html(date, data)
    html_path = SCRIPT_DIR / f"digest_{date.isoformat()}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved HTML:     {html_path.name}")

    # Write index.html (always = latest digest, for GitHub Pages)
    index_path = SCRIPT_DIR / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved index.html (GitHub Pages)")

    # Markdown
    md = generate_markdown(date, data)
    md_path = SCRIPT_DIR / f"digest_{date.isoformat()}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  Saved Markdown: {md_path.name}")

    # HN Archive
    print("  Updating HN archive…")
    update_hn_archive(date, data["hn_expanded"])

    # DD Archive + Google Sheets
    print("  Updating digest archive…")
    update_dd_archive(date, data, config)

    # GitHub Pages
    print("  GitHub Pages…")
    push_to_github(date, config)

    print(f"\n  ✓ Done!\n")
    return {
        "html_path": str(html_path),
        "md_path":   str(md_path),
        "html":      html,
        "md":        md,
        "date":      date.isoformat(),
        "subject":   f"Daily Digest — {date.strftime('%A, %b %-d')}",
    }

if __name__ == "__main__":
    main()
