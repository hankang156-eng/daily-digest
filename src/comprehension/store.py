"""Persistent state for the comprehension layer: the thread registry and marks.

Everything here is pure and filesystem-injectable (pass `path=`), so tests never
touch real state and never hit the network.

Thread identity rule: `slug` is immutable once created. `name` and `charter` may
be revised freely by the discovery pass, and a thread may be merged into another,
but a slug never changes meaning and is never deleted - merged and dormant
threads are retained with a status, consistent with the never-destroy convention.
"""

import datetime
import json
import re
import shutil
from pathlib import Path

from .paths import (
    DEFAULT_MARKS_DOWNLOAD_DIR,
    MARKS_ARCHIVE_DIR,
    MARKS_FILE,
    THREADS_FILE,
)

SCHEMA_VERSION = 1
TRACKS = ("ai-at-large", "ai-infra")
VALID_MARKS = ("knew-this", "useful", "over-my-head")
# Marks attach to explanations. The primary scope is a thread on the companion
# page (~10 a day); the digest only offers marks on items carrying a Context note
# (~3 a day). Namespacing thread keys keeps them distinct from item URLs.
THREAD_MARK_PREFIX = "thread:"

# A thread with no new item for this many days stops being rendered as active.
DEFAULT_DORMANT_AFTER_DAYS = 21
# Titles kept in the prompt as a thread's recent-history hint.
RECENT_TITLE_COUNT = 3


# --- json io -----------------------------------------------------------------

def load_json(path, default):
    path = Path(path)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [Comprehension] Error reading {path.name}: {e}")
    return default


def write_json(path, value):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        print(f"  [Comprehension] Error writing {path.name}: {e}")
        return False


# --- slugs -------------------------------------------------------------------

def slugify(name, max_length=40):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    if len(slug) > max_length:
        head = slug[:max_length]
        # Cut at a word boundary so a truncated slug still reads as a name.
        if slug[max_length] != "-" and "-" in head:
            head = head.rsplit("-", 1)[0]
        slug = head.strip("-")
    return slug or "thread"


def unique_slug(base, existing):
    slug = slugify(base)
    if slug not in existing:
        return slug
    for suffix in range(2, 100):
        candidate = f"{slug}-{suffix}"
        if candidate not in existing:
            return candidate
    raise ValueError(f"Could not derive a unique slug from {base!r}")


# --- registry ----------------------------------------------------------------

def new_registry():
    return {"schema_version": SCHEMA_VERSION, "updated": "", "threads": []}


def load_threads(path=THREADS_FILE):
    registry = load_json(path, None)
    if not isinstance(registry, dict) or "threads" not in registry:
        return new_registry()
    registry.setdefault("schema_version", SCHEMA_VERSION)
    registry.setdefault("updated", "")
    return registry


def save_threads(registry, path=THREADS_FILE, as_of=None):
    if as_of is not None:
        registry["updated"] = _iso(as_of)
    return write_json(path, registry)


def threads_by_slug(registry):
    return {thread["slug"]: thread for thread in registry.get("threads", [])}


def resolve_slug(registry, slug):
    """Follow merge redirects to the surviving thread. None if unknown.

    An unmatched reference is retried through slugify() before giving up. This
    matters because slugs are truncated to max_length while display names are
    not, so a caller reconstructing a slug from a thread's name produces a longer
    string than the stored slug ("...-gets-mixed-reception" vs "...-gets-mixed").
    Normalizing collapses that difference; without it every long-named thread
    spawns a duplicate that only gets folded back if a merge is noticed later.
    """
    resolved = _follow_redirects(registry, slug)
    if resolved is not None:
        return resolved
    normalized = slugify(slug)
    if normalized != slug:
        return _follow_redirects(registry, normalized)
    return None


def _follow_redirects(registry, slug):
    index = threads_by_slug(registry)
    seen = set()
    current = slug
    while current in index and current not in seen:
        seen.add(current)
        target = index[current].get("merged_into")
        if not target:
            return current
        current = target
    return current if current in index else None


def active_threads(registry):
    return [t for t in registry.get("threads", []) if t.get("status") == "active"]


def create_thread(registry, name, charter="", track="ai-at-large", as_of=None, slug=None):
    existing = set(threads_by_slug(registry))
    thread = {
        "slug": slug if slug and slug not in existing else unique_slug(slug or name, existing),
        "name": str(name or "").strip() or "Untitled thread",
        "charter": str(charter or "").strip(),
        "track": track if track in TRACKS else "ai-at-large",
        "created": _iso(as_of),
        "last_update": "",
        "status": "active",
        "merged_into": None,
        "items": [],
    }
    registry.setdefault("threads", []).append(thread)
    return thread


def update_thread(registry, slug, name=None, charter=None, track=None):
    """Revise display fields. Slug is never touched."""
    thread = threads_by_slug(registry).get(resolve_slug(registry, slug) or "")
    if not thread:
        return None
    if name:
        thread["name"] = str(name).strip()
    if charter:
        thread["charter"] = str(charter).strip()
    if track in TRACKS:
        thread["track"] = track
    return thread


def record_item(registry, slug, item, as_of=None, note=""):
    """File one digest item under a thread. Returns False if already present."""
    target = resolve_slug(registry, slug)
    thread = threads_by_slug(registry).get(target or "")
    if not thread:
        return False
    key = item.get("item_key") or item.get("url") or item.get("title")
    if any(entry.get("item_key") == key for entry in thread["items"]):
        return False
    thread["items"].append({
        "item_key": key,
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "source": item.get("source", ""),
        "group": item.get("group", ""),
        "digest_date": _iso(as_of),
        "note": str(note or "").strip(),
    })
    stamp = _iso(as_of)
    if stamp > (thread.get("last_update") or ""):
        thread["last_update"] = stamp
    if thread.get("status") == "dormant":
        thread["status"] = "active"
    return True


def merge_thread(registry, loser_slug, winner_slug, as_of=None, why=""):
    """Fold loser into winner. The loser is retained as a redirect, never deleted."""
    index = threads_by_slug(registry)
    loser = index.get(resolve_slug(registry, loser_slug) or "")
    winner = index.get(resolve_slug(registry, winner_slug) or "")
    if not loser or not winner or loser is winner:
        return False

    existing_keys = {entry.get("item_key") for entry in winner["items"]}
    for entry in loser["items"]:
        if entry.get("item_key") not in existing_keys:
            winner["items"].append(entry)
            existing_keys.add(entry.get("item_key"))
    winner["items"].sort(key=lambda entry: entry.get("digest_date", ""))
    winner["last_update"] = max(
        winner.get("last_update") or "", loser.get("last_update") or ""
    )
    if winner.get("status") != "active":
        winner["status"] = "active"

    loser["items"] = []
    loser["status"] = "merged"
    loser["merged_into"] = winner["slug"]
    loser["merged_on"] = _iso(as_of)
    if why:
        loser["merge_reason"] = str(why).strip()
    return True


def retire_dormant(registry, as_of, idle_days=DEFAULT_DORMANT_AFTER_DAYS):
    """Mark active threads with no recent item as dormant. Returns their slugs."""
    as_of_date = _as_date(as_of)
    if as_of_date is None:
        return []
    retired = []
    for thread in active_threads(registry):
        last = _as_date(thread.get("last_update") or thread.get("created"))
        if last is None:
            continue
        if (as_of_date - last).days > idle_days:
            thread["status"] = "dormant"
            retired.append(thread["slug"])
    return retired


def registry_summary(registry):
    threads = registry.get("threads", [])
    by_status = {}
    for thread in threads:
        by_status[thread.get("status", "unknown")] = by_status.get(thread.get("status", "unknown"), 0) + 1
    return {
        "total": len(threads),
        "by_status": by_status,
        "items": sum(len(thread.get("items", [])) for thread in threads),
    }


def thread_digest_for_prompt(thread, recent=RECENT_TITLE_COUNT):
    """Compact registry view handed to the discovery pass."""
    items = thread.get("items", [])
    return {
        "slug": thread["slug"],
        "name": thread.get("name", ""),
        "charter": thread.get("charter", ""),
        "track": thread.get("track", ""),
        "item_count": len(items),
        "last_update": thread.get("last_update", ""),
        "recent_titles": [entry.get("title", "") for entry in items[-recent:]],
    }


# --- marks -------------------------------------------------------------------

def new_marks():
    return {"schema_version": SCHEMA_VERSION, "items": {}}


def load_marks(path=MARKS_FILE):
    marks = load_json(path, None)
    if not isinstance(marks, dict) or not isinstance(marks.get("items"), dict):
        return new_marks()
    marks.setdefault("schema_version", SCHEMA_VERSION)
    return marks


def save_marks(marks, path=MARKS_FILE):
    return write_json(path, marks)


def merge_marks(marks, incoming):
    """Fold one exported marks payload in. Order-independent and idempotent.

    Conflicts resolve on (recorded_at, mark) - the later timestamp wins, and an
    identical timestamp resolves by mark name so the outcome never depends on
    which file was read first.
    """
    added = 0
    items = marks.setdefault("items", {})
    for key, record in _iter_incoming_marks(incoming):
        mark = record.get("mark")
        if mark not in VALID_MARKS:
            continue
        candidate = {
            "mark": mark,
            "recorded_at": str(record.get("recorded_at") or ""),
            "digest_date": str(record.get("digest_date") or ""),
        }
        current = items.get(key)
        if current is None or _mark_rank(candidate) > _mark_rank(current):
            items[key] = candidate
            added += 1
    return added


def harvest_marks(
    downloads_dir=DEFAULT_MARKS_DOWNLOAD_DIR,
    marks_path=MARKS_FILE,
    archive_dir=MARKS_ARCHIVE_DIR,
    pattern="dd-marks-*.json",
    move=True,
):
    """Absorb exported marks files and archive them.

    Returns {"files": n, "marks": n}. No files present is a no-op, never a
    demotion - silence must be safe.
    """
    downloads_dir = Path(downloads_dir)
    found = sorted(downloads_dir.glob(pattern)) if downloads_dir.is_dir() else []
    if not found:
        return {"files": 0, "marks": 0}

    marks = load_marks(marks_path)
    absorbed = 0
    consumed = []
    for path in found:
        payload = load_json(path, None)
        if payload is None:
            continue
        absorbed += merge_marks(marks, payload)
        consumed.append(path)

    if consumed:
        save_marks(marks, marks_path)
    if move and consumed:
        archive_dir = Path(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        for path in consumed:
            try:
                shutil.move(str(path), str(archive_dir / path.name))
            except Exception as e:
                print(f"  [Comprehension] Could not archive {path.name}: {e}")
    return {"files": len(consumed), "marks": absorbed}


def marks_hint(marks, limit=12):
    """Altitude hint for prompts: what the reader already knows vs. struggles with."""
    knew, hard = [], []
    for key, record in (marks.get("items") or {}).items():
        if record.get("mark") == "knew-this":
            knew.append(key)
        elif record.get("mark") == "over-my-head":
            hard.append(key)
    return {"knew_this": knew[-limit:], "over_my_head": hard[-limit:]}


def thread_mark_key(slug):
    """Marks on a thread are namespaced so they cannot collide with item URLs."""
    return f"{THREAD_MARK_PREFIX}{slug}"


def labels_by_mark_key(registry):
    """{mark key: human label} for both mark scopes.

    Item marks are keyed by URL and labelled with the article title; thread marks
    are keyed "thread:<slug>" and labelled with the thread's name. A thread label
    is the more useful signal, because a thread is a thing that was *explained* -
    so it says whether the explanation landed, not just whether the news was new.
    """
    labels = {}
    for thread in registry.get("threads", []):
        if thread.get("name"):
            labels[thread_mark_key(thread["slug"])] = f"the thread \"{thread['name']}\""
        for entry in thread.get("items", []):
            key = entry.get("item_key")
            if key and entry.get("title"):
                labels[key] = entry["title"]
    return labels


def marks_altitude_note(marks, registry, limit=8):
    """One short paragraph telling a prompt where to pitch its explanations.

    Returns "" when there is nothing to say - silence has to be safe, so no
    marks must mean no instruction rather than a demotion.
    """
    hint = marks_hint(marks, limit=limit)
    labels = labels_by_mark_key(registry)

    def named(keys):
        # Threads first: they are feedback on an explanation, not on a headline.
        ordered = sorted(keys, key=lambda key: not key.startswith(THREAD_MARK_PREFIX))
        return [labels[key] for key in ordered if labels.get(key)]

    knew, hard = named(hint["knew_this"]), named(hint["over_my_head"])
    if not knew and not hard:
        return ""
    lines = ["## Where to pitch this", ""]
    if knew:
        lines.append(
            "The reader marked these as things they already knew, so do not "
            "re-explain that ground: " + "; ".join(knew) + "."
        )
    if hard:
        lines.append(
            "The reader marked these as over their head, so start from first "
            "principles on anything like them: " + "; ".join(hard) + "."
        )
    return "\n".join(lines)


def _iter_incoming_marks(incoming):
    """Accept either {"items": {key: rec}} or a flat list of records."""
    if isinstance(incoming, dict):
        items = incoming.get("items", incoming)
        if isinstance(items, dict):
            for key, record in items.items():
                if isinstance(record, dict):
                    yield str(key), record
                elif isinstance(record, str):
                    yield str(key), {"mark": record}
            return
        incoming = items
    if isinstance(incoming, list):
        for record in incoming:
            if isinstance(record, dict) and record.get("item_key"):
                yield str(record["item_key"]), record


def _mark_rank(record):
    return (str(record.get("recorded_at") or ""), str(record.get("mark") or ""))


def _iso(value):
    if value is None:
        return datetime.date.today().isoformat()
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
