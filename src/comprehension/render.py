"""Companion page: the thread narrative for one digest date.

The digest answers "what should I read"; this page answers "what does it mean".
It reuses the digest's stylesheet and script so the two surfaces look and behave
the same (font/theme toggles, expand/collapse all).
"""

import datetime
import re
from pathlib import Path

from src.daily_digest import _PAGE_CSS, _PAGE_SCRIPT, _html_escape, _render_marks

from . import narrative as narrative_mod
from . import store
from .paths import OUTPUT_DIR

TRACK_LABELS = {
    "ai-at-large": "AI at large",
    "ai-infra": "AI infrastructure",
}
TRACK_ORDER = ("ai-infra", "ai-at-large")

COMPANION_FILE_RE = re.compile(r"comprehension_(?P<date>\d{4}-\d{2}-\d{2})\.html$")
WEEKLY_FILE_RE = re.compile(r"weekly_(?P<date>\d{4}-\d{2}-\d{2})\.html$")

COMPANION_CSS = """
.track { margin: 0 0 26px; }
.track-head {
  font-family: var(--font-meta); font-size: 11px; font-weight: 600;
  letter-spacing: .12em; text-transform: uppercase; color: var(--accent);
  padding-bottom: 6px; border-bottom: 1px solid var(--rule);
  margin: 0 0 4px;
}
.thread { padding: var(--row-pad) 0; border-bottom: 1px solid var(--rule-soft); }
.thread h3 {
  font-family: var(--font-body); font-size: calc(var(--body-size) + 3px);
  font-weight: 600; line-height: 1.2; margin: 0 0 3px; color: var(--ink);
}
.thread .headline {
  font-family: var(--font-body); font-size: var(--body-size);
  font-weight: 600; color: var(--accent); margin: 0 0 6px; text-wrap: pretty;
}
.thread p {
  font-family: var(--font-body); font-size: var(--body-size);
  line-height: 1.45; color: var(--ink-soft); margin: 0 0 7px; text-wrap: pretty;
}
.thread .matters { color: var(--ink); }
.thread .matters b, .thread .charter b {
  font-family: var(--font-meta); font-size: 10px; font-weight: 600;
  letter-spacing: .1em; text-transform: uppercase; color: var(--mute);
  display: block; margin-bottom: 2px;
}
.thread .charter { font-style: italic; color: var(--mute); }
.thread-meta {
  font-family: var(--font-meta); font-size: 11px; color: var(--mute);
  margin: 0 0 7px;
}
.sources { margin: 6px 0 0; padding: 0; list-style: none; }
.sources li {
  font-family: var(--font-meta); font-size: 12px;
  color: var(--mute); padding: 1px 0;
}
.sources a { color: var(--ink-soft); }
.sources a:hover { color: var(--hover); }
.quiet { margin: 0; padding: 0; list-style: none; }
.quiet li {
  font-family: var(--font-meta); font-size: 12px; color: var(--mute);
  padding: 2px 0; border-bottom: 1px solid var(--rule-soft);
}
.empty { font-family: var(--font-body); color: var(--mute); font-style: italic; }
"""


def _plural(count, noun):
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _thread_meta_text(thread, today_count):
    items = thread.get("items", [])
    first = items[0].get("digest_date") if items else ""
    # "new" is already correct for any count; pluralizing it yields "5 news today".
    parts = [_plural(len(items), "item"), f"{today_count} new today"]
    if first:
        parts.append(f"tracked since {first}")
    return " · ".join(parts)


def _grouped_by_track(moved):
    groups = {}
    for thread, today in moved:
        groups.setdefault(thread.get("track") or "ai-at-large", []).append((thread, today))
    ordered = [(track, groups.pop(track)) for track in TRACK_ORDER if track in groups]
    ordered += sorted(groups.items())
    return ordered


# --- markdown ----------------------------------------------------------------

def render_markdown(digest_date, registry, narratives, items_by_key=None):
    moved = narrative_mod.threads_that_moved(registry, digest_date)
    quiet = narrative_mod.quiet_threads(registry, digest_date)
    lines = [
        f"# AI Comprehension — {_long_date(digest_date)}",
        "",
        f"*Threads that moved: {len(moved)} · quiet: {len(quiet)}*",
        "",
        "---",
        "",
    ]
    if not moved:
        lines += ["*No thread moved today.*", ""]

    for track, entries in _grouped_by_track(moved):
        lines += [f"### {TRACK_LABELS.get(track, track)}", ""]
        for thread, today in entries:
            note = narratives.get(thread["slug"]) or {}
            lines.append(f"#### {thread.get('name')}")
            lines.append(f"*{_thread_meta_text(thread, len(today))}*")
            lines.append("")
            if note.get("headline"):
                lines += [f"**{note['headline']}**", ""]
            if note.get("what_changed"):
                lines += [note["what_changed"], ""]
            if note.get("why_it_matters"):
                lines += [f"**Why it matters:** {note['why_it_matters']}", ""]
            for entry in today:
                url = entry.get("url") or "#"
                lines.append(f"- [{entry.get('title')}]({url}) — {entry.get('source')}")
            lines.append("")

    if quiet:
        lines += ["### Quiet threads", ""]
        for thread in sorted(quiet, key=lambda t: t.get("last_update") or "", reverse=True):
            lines.append(
                f"- {thread.get('name')} — last moved {thread.get('last_update') or 'n/a'}"
            )
        lines.append("")
    return "\n".join(lines)


# --- html --------------------------------------------------------------------

def _render_thread_html(thread, today, note):
    sources = "".join(
        '<li><a href="{url}" target="_blank">{title}</a> · {source}</li>'.format(
            url=_html_escape(entry.get("url") or "#"),
            title=_html_escape(entry.get("title", "")),
            source=_html_escape(entry.get("source", "")),
        )
        for entry in today
    )
    blocks = [
        f'<h3>{_html_escape(thread.get("name", ""))}</h3>',
        f'<div class="thread-meta">{_html_escape(_thread_meta_text(thread, len(today)))}</div>',
    ]
    if note.get("headline"):
        blocks.append(f'<p class="headline">{_html_escape(note["headline"])}</p>')
    if note.get("what_changed"):
        blocks.append(f'<p>{_html_escape(note["what_changed"])}</p>')
    if note.get("why_it_matters"):
        blocks.append(
            f'<p class="matters"><b>Why it matters</b>{_html_escape(note["why_it_matters"])}</p>'
        )
    if not note:
        blocks.append('<p class="empty">No narrative was generated for this thread today.</p>')
    if thread.get("charter"):
        blocks.append(f'<p class="charter"><b>Thread</b>{_html_escape(thread["charter"])}</p>')
    if sources:
        blocks.append(f'<ul class="sources">{sources}</ul>')
    # The primary feedback surface. A thread is a thing that was explained, so a
    # mark here says whether the explanation landed - unambiguous in a way a mark
    # on a bare headline is not, and ~10 a day rather than ~67.
    blocks.append(_render_marks(store.thread_mark_key(thread["slug"]), "How this explanation landed"))
    return '<div class="thread">\n  ' + "\n  ".join(blocks) + "\n</div>"


def render_html(
    digest_date,
    registry,
    narratives,
    items_by_key=None,
    digest_href=None,
    archive_href="comprehension_archive.html",
):
    moved = narrative_mod.threads_that_moved(registry, digest_date)
    quiet = narrative_mod.quiet_threads(registry, digest_date)
    date_str = _long_date(digest_date)
    if digest_href is None:
        digest_href = f"../daily_html/digest_{digest_date}.html"

    body = []
    for track, entries in _grouped_by_track(moved):
        threads = "\n".join(
            _render_thread_html(thread, today, narratives.get(thread["slug"]) or {})
            for thread, today in entries
        )
        body.append(
            f'<section class="track">\n'
            f'  <div class="track-head">{_html_escape(TRACK_LABELS.get(track, track))}</div>\n'
            f"{threads}\n</section>"
        )
    if not moved:
        body.append('<p class="empty">No thread moved today.</p>')

    if quiet:
        items = "".join(
            "<li>{name} — last moved {last}</li>".format(
                name=_html_escape(thread.get("name", "")),
                last=_html_escape(thread.get("last_update") or "n/a"),
            )
            for thread in sorted(quiet, key=lambda t: t.get("last_update") or "", reverse=True)
        )
        body.append(
            '<section class="track">\n'
            '  <div class="track-head">Quiet threads</div>\n'
            f'  <ul class="quiet">{items}</ul>\n</section>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Comprehension — {_html_escape(date_str)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400..700;1,400..700&family=Inter:wght@400;500;600&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">
<style>
{_PAGE_CSS}
{COMPANION_CSS}
</style>
</head>
<body>
<div class="page" data-digest-date="{_html_escape(str(digest_date))}">
  <div class="toolbar">
    <a class="archive" href="{_html_escape(digest_href)}" title="Read the digest for this day"><span class="ico" aria-hidden="true">←</span><span class="lbl"> Read this day's digest</span></a>
    <div class="controls">
      <div class="seg" role="group" aria-label="Font">
        <button class="btn" data-tg="font" data-val="serif" aria-pressed="true" title="Serif"><span class="ico ico-serif" aria-hidden="true">A</span><span class="lbl"> Serif</span></button>
        <button class="btn" data-tg="font" data-val="sans" aria-pressed="false" title="Sans"><span class="ico ico-sans" aria-hidden="true">A</span><span class="lbl"> Sans</span></button>
      </div>
      <div class="seg" role="group" aria-label="Theme">
        <button class="btn" data-tg="theme" data-val="light" aria-pressed="true" title="Light"><span class="ico" aria-hidden="true">☀</span><span class="lbl"> Light</span></button>
        <button class="btn" data-tg="theme" data-val="dark" aria-pressed="false" title="Dark"><span class="ico" aria-hidden="true">☾</span><span class="lbl"> Dark</span></button>
      </div>
      <button class="btn" id="saveMarks" title="Download your marks so the next run can use them"><span class="ico" aria-hidden="true">⇩</span><span class="lbl"> Save marks</span></button>
      <a class="btn" href="{_html_escape(archive_href)}" title="Past comprehension pages"><span class="ico" aria-hidden="true">☰</span><span class="lbl"> Archive</span></a>
    </div>
  </div>

  <header class="masthead">
    <div class="kicker">What It Means</div>
    <h1 class="wordmark">{_html_escape(date_str)}</h1>
    <div class="fetched">{len(moved)} threads moved · {len(quiet)} quiet</div>
  </header>

{chr(10).join(body)}

</div>
<script>
{_PAGE_SCRIPT}
</script>
</body>
</html>
"""


# --- writing and archive -----------------------------------------------------

def write_companion(digest_date, registry, narratives, items_by_key=None, output_dir=OUTPUT_DIR):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / f"comprehension_{digest_date}.html"
    md_path = output_dir / f"comprehension_{digest_date}.md"
    html_path.write_text(
        render_html(digest_date, registry, narratives, items_by_key), encoding="utf-8"
    )
    md_path.write_text(
        render_markdown(digest_date, registry, narratives, items_by_key), encoding="utf-8"
    )
    return {"html_path": html_path, "md_path": md_path}


# --- weekly synthesis --------------------------------------------------------

WEEKLY_SECTIONS = (
    ("what_happened", "What happened"),
    ("what_was_noise", "What was noise"),
    ("what_to_watch", "What to watch"),
)


def render_weekly_markdown(essay):
    start, end = essay.get("week_start", ""), essay.get("week_end", "")
    lines = [
        f"# The week in AI — {_long_date(end)}",
        "",
        f"*{start} to {end} · {essay.get('model', '')}*",
        "",
        "---",
        "",
    ]
    if essay.get("headline"):
        lines += [f"**{essay['headline']}**", ""]
    for field, label in WEEKLY_SECTIONS:
        if essay.get(field):
            lines += [f"### {label}", "", essay[field], ""]
    return "\n".join(lines)


def render_weekly_html(essay, archive_href="comprehension_archive.html"):
    start, end = essay.get("week_start", ""), essay.get("week_end", "")
    date_str = _long_date(end)
    body = []
    if essay.get("headline"):
        body.append(f'<p class="headline">{_html_escape(essay["headline"])}</p>')
    for field, label in WEEKLY_SECTIONS:
        if not essay.get(field):
            continue
        paragraphs = "".join(
            f"<p>{_html_escape(chunk.strip())}</p>"
            for chunk in re.split(r"\n\s*\n", essay[field])
            if chunk.strip()
        )
        body.append(
            f'<section class="track">\n'
            f'  <div class="track-head">{_html_escape(label)}</div>\n'
            f'  <div class="thread">{paragraphs}</div>\n</section>'
        )
    if not body:
        body.append('<p class="empty">No synthesis was generated for this week.</p>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The week in AI — {_html_escape(date_str)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400..700;1,400..700&family=Inter:wght@400;500;600&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">
<style>
{_PAGE_CSS}
{COMPANION_CSS}
</style>
</head>
<body>
<div class="page">
  <div class="toolbar">
    <a class="archive" href="{_html_escape(archive_href)}"><span class="ico" aria-hidden="true">←</span><span class="lbl"> Comprehension archive</span></a>
    <div class="controls">
      <div class="seg" role="group" aria-label="Font">
        <button class="btn" data-tg="font" data-val="serif" aria-pressed="true" title="Serif"><span class="ico ico-serif" aria-hidden="true">A</span><span class="lbl"> Serif</span></button>
        <button class="btn" data-tg="font" data-val="sans" aria-pressed="false" title="Sans"><span class="ico ico-sans" aria-hidden="true">A</span><span class="lbl"> Sans</span></button>
      </div>
      <div class="seg" role="group" aria-label="Theme">
        <button class="btn" data-tg="theme" data-val="light" aria-pressed="true" title="Light"><span class="ico" aria-hidden="true">☀</span><span class="lbl"> Light</span></button>
        <button class="btn" data-tg="theme" data-val="dark" aria-pressed="false" title="Dark"><span class="ico" aria-hidden="true">☾</span><span class="lbl"> Dark</span></button>
      </div>
    </div>
  </div>

  <header class="masthead">
    <div class="kicker">The Week In AI</div>
    <h1 class="wordmark">{_html_escape(date_str)}</h1>
    <div class="fetched">{_html_escape(start)} to {_html_escape(end)}</div>
  </header>

{chr(10).join(body)}

</div>
<script>
{_PAGE_SCRIPT}
</script>
</body>
</html>
"""


def write_weekly(essay, output_dir=OUTPUT_DIR):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    end = essay.get("week_end", "")
    html_path = output_dir / f"weekly_{end}.html"
    md_path = output_dir / f"weekly_{end}.md"
    html_path.write_text(render_weekly_html(essay), encoding="utf-8")
    md_path.write_text(render_weekly_markdown(essay), encoding="utf-8")
    return {"html_path": html_path, "md_path": md_path}


# --- archive -----------------------------------------------------------------

def _dated_entries(output_dir, pattern, regex):
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return []
    entries = []
    for path in output_dir.glob(pattern):
        match = regex.search(path.name)
        if not match:
            continue
        date_str = match.group("date")
        entries.append((date_str, f"{date_str} ({_weekday(date_str)})", path.name))
    return sorted(entries, reverse=True)


def companion_archive_entries(output_dir=OUTPUT_DIR):
    return _dated_entries(output_dir, "comprehension_*.html", COMPANION_FILE_RE)


def weekly_archive_entries(output_dir=OUTPUT_DIR):
    return _dated_entries(output_dir, "weekly_*.html", WEEKLY_FILE_RE)


def generate_companion_archive_html(entries, weekly_entries=(), digest_archive_href="../../digest_archive.html"):
    rows = "".join(
        f'<li><a href="{_html_escape(href)}">{_html_escape(label)}</a></li>'
        for _, label, href in entries
    ) or '<li class="empty">No comprehension pages yet.</li>'
    weekly_section = ""
    if weekly_entries:
        weekly_rows = "".join(
            f'<li><a href="{_html_escape(href)}">Week ending {_html_escape(label)}</a></li>'
            for _, label, href in weekly_entries
        )
        weekly_section = (
            '  <section class="track">\n'
            '    <div class="track-head">Weekly synthesis</div>\n'
            f'    <ul class="quiet">{weekly_rows}</ul>\n  </section>\n'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Comprehension — Archive</title>
<style>
{_PAGE_CSS}
{COMPANION_CSS}
</style>
</head>
<body>
<div class="page">
  <div class="toolbar">
    <a class="archive" href="{_html_escape(digest_archive_href)}"><span class="ico" aria-hidden="true">←</span><span class="lbl"> Digest archive</span></a>
  </div>
  <header class="masthead">
    <div class="kicker">What It Means</div>
    <h1 class="wordmark">Archive</h1>
    <div class="fetched">{len(entries)} pages</div>
  </header>
{weekly_section}  <section class="track">
    <div class="track-head">Daily, newest first</div>
    <ul class="quiet">{rows}</ul>
  </section>
</div>
</body>
</html>
"""


def write_companion_archive_page(output_dir=OUTPUT_DIR):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "comprehension_archive.html"
    path.write_text(
        generate_companion_archive_html(
            companion_archive_entries(output_dir), weekly_archive_entries(output_dir)
        ),
        encoding="utf-8",
    )
    return path


def _as_date(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _long_date(value):
    day = _as_date(value)
    return day.strftime("%A, %B %-d, %Y") if day else str(value)


def _weekday(value):
    day = _as_date(value)
    return day.strftime("%A") if day else ""


__all__ = [
    "COMPANION_CSS",
    "companion_archive_entries",
    "generate_companion_archive_html",
    "render_html",
    "render_markdown",
    "render_weekly_html",
    "render_weekly_markdown",
    "weekly_archive_entries",
    "write_companion",
    "write_companion_archive_page",
    "write_weekly",
]
