"""Per-thread "what changed today" narrative — the daily comprehension payload.

One batched call per digest date covering every thread that moved, so the model
can see the day whole and the result is a single cache entry. Narratives are
persisted per date so a companion page can be re-rendered without re-calling.
"""

from . import store
from .paths import NARRATIVES_FILE

NARRATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["threads"],
    "properties": {
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slug", "headline", "what_changed", "why_it_matters"],
                "properties": {
                    "slug": {"type": "string"},
                    "headline": {"type": "string"},
                    "what_changed": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                },
            },
        },
    },
}

NARRATIVE_SYSTEM = """\
You write the daily update for a set of running storylines the reader already \
follows. Your job is comprehension, not summary: after reading you, the reader \
should understand the story better than they did yesterday.

For each thread, write three things:

`headline`  - one short line naming today's movement. Not the article's \
headline; the development. "Second hyperscaler commits to 800V racks" beats \
"Google announces data center plans".

`what_changed` - two or three sentences. Assume the reader knows the thread \
already, because they do: reference where the story stood and say what is \
different now. Do not re-explain the thread from scratch, and do not summarize \
each article in turn.

`why_it_matters` - two or three sentences at the reader's altitude. This is the \
part that builds fluency, so use it to supply what the reader would need in \
order to follow the story further: the mechanism behind the news, the piece of \
jargon that is load-bearing, why this is happening now, or what would count as \
the next real move. If the reader is hazy on something the story depends on, \
explain that thing here rather than assuming it.

Rules:
  - Ground every claim in the items given. If the items do not support a \
conclusion, do not draw it. You may explain background the items assume, but \
never invent events, numbers, or quotes.
  - Write plain prose in complete sentences. No markdown, no bullets, no \
headers, no arrow chains.
  - Say plainly when a day's movement is minor. "Nothing decisive happened, but \
X is now the third company to Y" is a useful and honest update.
  - Return one entry per thread you were given, keyed by the exact slug.
"""

# How much prior history the model sees per thread.
HISTORY_LIMIT = 6
PROSE_LIMIT = 700


def threads_that_moved(registry, digest_date):
    """[(thread, entries_filed_on_that_date)] for active threads, busiest first."""
    moved = []
    for thread in store.active_threads(registry):
        today = [entry for entry in thread.get("items", []) if entry.get("digest_date") == digest_date]
        if today:
            moved.append((thread, today))
    moved.sort(key=lambda pair: len(pair[1]), reverse=True)
    return moved


def quiet_threads(registry, digest_date):
    """Active threads that existed by this date but did not move on it.

    The created-date check matters when re-rendering an earlier date: without it
    the page would list threads that had not been discovered yet.
    """
    return [
        thread
        for thread in store.active_threads(registry)
        if (thread.get("created") or "") <= str(digest_date)
        and not any(entry.get("digest_date") == digest_date for entry in thread.get("items", []))
    ]


def build_prompt(moved, digest_date, items_by_key=None, profile_text=""):
    items_by_key = items_by_key or {}
    parts = []
    if profile_text:
        parts += ["# Reader", "", profile_text.strip(), ""]
    parts += [f"# Threads that moved on {digest_date}", ""]

    for thread, today in moved:
        parts.append(f"## {thread['slug']} — \"{thread.get('name')}\" [{thread.get('track')}]")
        if thread.get("charter"):
            parts.append(f"Charter: {thread['charter']}")
        history = [
            entry for entry in thread.get("items", [])
            if entry.get("digest_date") != digest_date
        ][-HISTORY_LIMIT:]
        if history:
            parts.append("")
            parts.append(f"Where the story stood ({len(thread.get('items', [])) - len(today)} earlier items):")
            for entry in history:
                note = f" — {entry['note']}" if entry.get("note") else ""
                parts.append(f"  - {entry.get('digest_date')}: {entry.get('title')}{note}")
        else:
            parts.append("")
            parts.append("This thread is new today; there is no earlier history.")

        parts.append("")
        parts.append("Today:")
        for entry in today:
            note = f" — {entry['note']}" if entry.get("note") else ""
            parts.append(f"  - {entry.get('title')} ({entry.get('source')}){note}")
            prose = (items_by_key.get(entry.get("item_key"), {}).get("prose") or "").replace("\n", " ").strip()
            if prose:
                parts.append(f"      {prose[:PROSE_LIMIT]}")
        parts.append("")

    parts.append(f"Write the update for each of the {len(moved)} threads above.")
    return "\n".join(parts)


def narrate_day(registry, digest_date, client, items_by_key=None, profile_text="", effort=None):
    """Return {slug: {headline, what_changed, why_it_matters}} for threads that moved."""
    moved = threads_that_moved(registry, digest_date)
    if not moved:
        return {}
    prompt = build_prompt(moved, digest_date, items_by_key, profile_text)
    response = client.complete(prompt, system=NARRATIVE_SYSTEM, schema=NARRATIVE_SCHEMA, effort=effort)
    if not isinstance(response, dict):
        return {}

    known = {thread["slug"] for thread, _ in moved}
    narratives = {}
    for entry in response.get("threads") or []:
        slug = str(entry.get("slug") or "").strip()
        if slug in known:
            narratives[slug] = {
                "headline": str(entry.get("headline") or "").strip(),
                "what_changed": str(entry.get("what_changed") or "").strip(),
                "why_it_matters": str(entry.get("why_it_matters") or "").strip(),
            }
    return narratives


# --- persistence -------------------------------------------------------------

def load_narratives(path=NARRATIVES_FILE):
    data = store.load_json(path, None)
    if not isinstance(data, dict):
        return {"schema_version": store.SCHEMA_VERSION, "dates": {}}
    data.setdefault("schema_version", store.SCHEMA_VERSION)
    data.setdefault("dates", {})
    return data


def save_narratives(data, path=NARRATIVES_FILE):
    return store.write_json(path, data)


def record_narratives(data, digest_date, narratives):
    data.setdefault("dates", {})[digest_date] = narratives
    return data


def narratives_for(data, digest_date):
    return (data.get("dates") or {}).get(digest_date) or {}
