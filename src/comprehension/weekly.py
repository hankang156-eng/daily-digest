"""Weekly synthesis: one Opus essay over the week's thread state.

The daily companion page answers "what moved today". This answers the harder
question the daily view cannot: across the week, what actually happened, what was
noise, and what to watch. It runs on Opus rather than the daily Sonnet because
separating signal from noise across seven days of threads is the one judgement
call in the system worth paying for.
"""

import datetime

from . import narrative as narrative_mod
from . import store
from .llm import DEFAULT_WEEKLY_MODEL
from .paths import WEEKLY_FILE

DEFAULT_WINDOW_DAYS = 7

WEEKLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "what_happened", "what_was_noise", "what_to_watch"],
    "properties": {
        "headline": {"type": "string"},
        "what_happened": {"type": "string"},
        "what_was_noise": {"type": "string"},
        "what_to_watch": {"type": "string"},
    },
}

WEEKLY_SYSTEM = """\
You write one weekly synthesis for a reader who has been following a set of \
running storylines. They have already seen the daily updates; do not recap them.

Write four things:

`headline` - one line naming the week's real development.

`what_happened` - three to five paragraphs. Synthesize across threads rather than \
walking through them one at a time: say which stories actually advanced, how they \
connect, and what the week means taken together. Where two threads are really one \
story, say so. Where a thread went quiet, that is information too.

`what_was_noise` - one or two paragraphs. Name what looked significant this week \
but was not, and say why. Be specific and willing to call something \
overhyped when the week's evidence says so. If genuinely everything mattered, say \
that plainly instead of manufacturing a dismissal.

`what_to_watch` - one or two paragraphs. What would count as the next real move \
in the stories that matter, stated concretely enough that the reader will \
recognize it when it appears.

Rules:
  - Ground everything in the thread material provided. You may supply background \
and mechanism the reader needs, but never invent events, numbers, or quotes.
  - Plain prose in complete sentences. No markdown, no bullets, no headers.
  - Write for someone who wants to understand the field, not a summary of \
articles. Explain the mechanism when it is what makes the week's news meaningful.
  - Prefer being useful over being comprehensive. A thread that did not matter \
this week can go unmentioned.
"""


def week_dates(end_date, days=DEFAULT_WINDOW_DAYS):
    """Inclusive list of ISO dates ending at end_date."""
    end = _as_date(end_date)
    if end is None:
        return []
    return [(end - datetime.timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def threads_in_window(registry, dates):
    """[(thread, entries_in_window)] for threads that moved during the window."""
    window = set(dates)
    active = []
    for thread in registry.get("threads", []):
        if thread.get("status") == "merged":
            continue
        entries = [e for e in thread.get("items", []) if e.get("digest_date") in window]
        if entries:
            active.append((thread, entries))
    active.sort(key=lambda pair: len(pair[1]), reverse=True)
    return active


def build_prompt(registry, narratives_data, dates, profile_text=""):
    active = threads_in_window(registry, dates)
    parts = []
    if profile_text:
        parts += ["# Reader", "", profile_text.strip(), ""]
    parts += [f"# The week of {dates[0]} to {dates[-1]}", ""]
    if not active:
        parts.append("(no thread moved this week)")

    for thread, entries in active:
        earlier = len(thread.get("items", [])) - len(entries)
        parts.append(f"## \"{thread.get('name')}\" [{thread.get('track')}] — {thread['slug']}")
        if thread.get("charter"):
            parts.append(f"Charter: {thread['charter']}")
        parts.append(
            f"{len(entries)} item(s) this week; {earlier} earlier item(s); "
            f"status {thread.get('status')}"
        )
        parts.append("")
        for date in dates:
            day_entries = [e for e in entries if e.get("digest_date") == date]
            if not day_entries:
                continue
            note = narrative_mod.narratives_for(narratives_data, date).get(thread["slug"]) or {}
            headline = note.get("headline")
            parts.append(f"  {date}" + (f" — {headline}" if headline else ""))
            for entry in day_entries:
                item_note = f" — {entry['note']}" if entry.get("note") else ""
                parts.append(f"    - {entry.get('title')} ({entry.get('source')}){item_note}")
            if note.get("what_changed"):
                parts.append(f"    what changed: {note['what_changed']}")
        parts.append("")

    quiet = [
        thread for thread in store.active_threads(registry)
        if not any(e.get("digest_date") in set(dates) for e in thread.get("items", []))
    ]
    if quiet:
        parts += ["## Threads that stayed quiet all week", ""]
        for thread in quiet:
            parts.append(f"  - \"{thread.get('name')}\" (last moved {thread.get('last_update') or 'n/a'})")
        parts.append("")

    parts.append("Write the weekly synthesis.")
    return "\n".join(parts)


def synthesize_week(registry, narratives_data, end_date, client, profile_text="", days=DEFAULT_WINDOW_DAYS):
    """Return the essay dict, or None when there is nothing to synthesize."""
    dates = week_dates(end_date, days)
    if not dates or not threads_in_window(registry, dates):
        return None
    prompt = build_prompt(registry, narratives_data, dates, profile_text)
    response = client.complete(prompt, system=WEEKLY_SYSTEM, schema=WEEKLY_SCHEMA)
    if not isinstance(response, dict):
        return None
    essay = {field: str(response.get(field) or "").strip() for field in WEEKLY_SCHEMA["required"]}
    essay["week_start"] = dates[0]
    essay["week_end"] = dates[-1]
    essay["model"] = client.model
    return essay


# --- persistence -------------------------------------------------------------

def load_weeklies(path=WEEKLY_FILE):
    data = store.load_json(path, None)
    if not isinstance(data, dict):
        return {"schema_version": store.SCHEMA_VERSION, "weeks": {}}
    data.setdefault("schema_version", store.SCHEMA_VERSION)
    data.setdefault("weeks", {})
    return data


def save_weeklies(data, path=WEEKLY_FILE):
    return store.write_json(path, data)


def record_week(data, end_date, essay):
    data.setdefault("weeks", {})[str(end_date)] = essay
    return data


def week_for(data, end_date):
    return (data.get("weeks") or {}).get(str(end_date)) or {}


def default_weekly_model(settings=None):
    return (settings or {}).get("weekly_model") or DEFAULT_WEEKLY_MODEL


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
