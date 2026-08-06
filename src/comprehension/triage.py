"""Thread discovery and filing: one batched call per digest day.

Threads are discovered by the model, not seeded by hand - but discovery is
incremental, not amnesiac. Every run sees the existing registry and is asked to
file into it by preference, opening a new thread only when nothing fits. That is
what keeps a storyline's identity stable across weeks, which is the whole point
of tracking threads rather than summarizing items.
"""

from . import store

# A single run inventing dozens of threads is a runaway, not discovery. This is a
# per-run guard, not a cap on the registry; overflow is logged, never silent.
DEFAULT_MAX_NEW_THREADS = 3
# How much of an item's prose the model sees. Enough to judge the storyline.
PROSE_LIMIT = 400

TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["new_threads", "assignments", "merges"],
    "properties": {
        "new_threads": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "name", "charter", "track"],
                "properties": {
                    "key": {"type": "string"},
                    "name": {"type": "string"},
                    "charter": {"type": "string"},
                    "track": {"type": "string", "enum": list(store.TRACKS)},
                },
            },
        },
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item", "thread", "note", "promote"],
                "properties": {
                    "item": {"type": "integer"},
                    "thread": {"type": "string"},
                    "note": {"type": "string"},
                    "promote": {"type": "boolean"},
                },
            },
        },
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["loser", "winner", "why"],
                "properties": {
                    "loser": {"type": "string"},
                    "winner": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
    },
}

TRIAGE_SYSTEM = """\
You maintain a set of running storylines ("threads") for one reader, from a \
daily news digest. The goal is fluency through continuity: the reader should \
experience each day's news as an update to a story they already understand, not \
as a pile of new things to learn.

Two tracks exist and must not be mixed:
  ai-at-large  - the AI conversation everyone is having: model releases, \
research, dev tooling, policy, industry moves.
  ai-infra     - the AI infrastructure the reader sells into: data-center \
power and cooling, rack-level electrical architecture, DC power distribution, \
silicon-carbide and power-semiconductor supply, hyperscaler capex and buildout, \
grid interconnect and siting, standards bodies.

Rules, in priority order:

1. Prefer an existing thread. If an item continues a story already in the \
registry, file it there even when the wording differs. Only open a new thread \
when no existing thread genuinely covers it.
2. Most items belong to no thread. A thread is a story that will still matter \
in a month. One-off curiosities, product launches with no follow-through, and \
routine coverage should be left unassigned - simply omit them from \
`assignments`. Filing everything defeats the purpose.
3. A new thread needs a durable story behind it, not a single article. Prefer \
opening one when two or more of today's items point at it, or when it clearly \
extends something the reader will keep seeing.
4. Name threads for the story, not the topic: "800V DC arrives in the rack" \
beats "data center power". The charter is one sentence saying what the story is \
and what would count as it developing.
5. Propose a merge when two existing threads have converged into one story. \
Name the thread with more history as `winner`.

Field notes:
  - `assignments[].thread` must be either (a) a slug copied exactly from the \
registry above, or (b) the `key` of a thread you declare in `new_threads` in \
this same response. There is no third option. Do not invent a slug for a thread \
you have not declared - if you want a new thread, declare it in `new_threads` \
first and reference its `key`. For example, to open one new thread and file two \
items into it:
      "new_threads": [{"key": "n1", "name": "...", "charter": "...", "track": "ai-infra"}]
      "assignments": [{"item": 4, "thread": "n1", ...}, {"item": 9, "thread": "n1", ...}]
  - `assignments[].note` is one short sentence on what this item adds to that \
thread - the movement, not a summary of the article.
  - `assignments[].promote` marks the few items worth reading in full to \
explain the thread properly. Set it true sparingly.
"""


def build_prompt(items, registry, digest_date, profile_text="", max_new_threads=DEFAULT_MAX_NEW_THREADS):
    parts = []
    if profile_text:
        parts += ["# Reader", "", profile_text.strip(), ""]

    parts += [f"# Existing threads ({digest_date})", ""]
    active = store.active_threads(registry)
    if not active:
        parts.append("(none yet - this is the first run)")
    for thread in sorted(active, key=lambda t: t.get("last_update") or "", reverse=True):
        digest = store.thread_digest_for_prompt(thread)
        parts.append(
            f"- {digest['slug']} [{digest['track']}] \"{digest['name']}\" "
            f"({digest['item_count']} items, last {digest['last_update'] or 'n/a'})"
        )
        if digest["charter"]:
            parts.append(f"    charter: {digest['charter']}")
        for title in digest["recent_titles"]:
            parts.append(f"    recent: {title}")
    parts.append("")

    parts += [f"# Today's items ({len(items)})", ""]
    for index, item in enumerate(items, 1):
        meta = " · ".join(
            str(part) for part in (item.get("source"), item.get("section"), item.get("group")) if part
        )
        parts.append(f"[{index}] {item.get('title', '')}")
        parts.append(f"    {meta}")
        prose = (item.get("prose") or "").replace("\n", " ").strip()
        if prose:
            parts.append(f"    {prose[:PROSE_LIMIT]}")
    parts.append("")

    parts.append(
        f"File today's items. Open at most {max_new_threads} new threads. "
        "Leave anything that is not part of a durable storyline unassigned."
    )
    return "\n".join(parts)


def triage_day(
    items,
    registry,
    client,
    digest_date,
    profile_text="",
    max_new_threads=DEFAULT_MAX_NEW_THREADS,
    idle_days=store.DEFAULT_DORMANT_AFTER_DAYS,
    effort=None,
    verbose=True,
):
    """Discover/file one day's items into the registry, mutating it in place."""
    result = {
        "items": len(items),
        "filed": 0,
        "duplicates": 0,
        "created": 0,
        "merged": 0,
        "retired": 0,
        "promoted": [],
        "unknown_threads": [],
        "dropped_new_threads": 0,
        "recovered_threads": 0,
        "ok": False,
    }
    if not items:
        return result

    prompt = build_prompt(items, registry, digest_date, profile_text, max_new_threads)
    response = client.complete(prompt, system=TRIAGE_SYSTEM, schema=TRIAGE_SCHEMA, effort=effort)
    if not isinstance(response, dict):
        return result
    result["ok"] = True

    result.update(
        apply_triage(
            response,
            items,
            registry,
            digest_date,
            max_new_threads=max_new_threads,
            idle_days=idle_days,
            verbose=verbose,
        )
    )
    return result


def apply_triage(
    response,
    items,
    registry,
    digest_date,
    max_new_threads=DEFAULT_MAX_NEW_THREADS,
    idle_days=store.DEFAULT_DORMANT_AFTER_DAYS,
    verbose=True,
):
    """Pure registry mutation from a validated triage response."""
    counts = {
        "filed": 0,
        "duplicates": 0,
        "created": 0,
        "merged": 0,
        "retired": 0,
        "promoted": [],
        "unknown_threads": [],
        "dropped_new_threads": 0,
        "recovered_threads": 0,
    }

    requested = response.get("new_threads") or []
    if len(requested) > max_new_threads:
        counts["dropped_new_threads"] = len(requested) - max_new_threads
        if verbose:
            print(
                f"  [Comprehension] {digest_date}: capped new threads at {max_new_threads}; "
                f"dropped {counts['dropped_new_threads']} "
                f"({', '.join(str(t.get('name', '?')) for t in requested[max_new_threads:])})"
            )
        requested = requested[:max_new_threads]

    new_slugs = {}
    for spec in requested:
        key = str(spec.get("key") or spec.get("name") or "").strip()
        if not key:
            continue
        thread = store.create_thread(
            registry,
            name=spec.get("name"),
            charter=spec.get("charter"),
            track=spec.get("track"),
            as_of=digest_date,
        )
        new_slugs[key] = thread["slug"]
        counts["created"] += 1

    # Two passes. The model occasionally references a thread by a slug it made up
    # rather than declaring it in new_threads; dropping those assignments can cost
    # most of a day's filing, so unresolved references are recovered as threads on
    # a second pass instead of discarded.
    resolved, deferred = [], {}
    for assignment in response.get("assignments") or []:
        index = assignment.get("item")
        if not isinstance(index, int) or not 1 <= index <= len(items):
            continue
        target = str(assignment.get("thread") or "").strip()
        slug = new_slugs.get(target) or store.resolve_slug(registry, target)
        if slug:
            resolved.append((slug, items[index - 1], assignment))
        elif target:
            deferred.setdefault(target, []).append((items[index - 1], assignment))

    for target, pending in deferred.items():
        if counts["created"] >= max_new_threads:
            counts["unknown_threads"].append(target)
            continue
        thread = store.create_thread(
            registry,
            name=_name_from_slug(target),
            charter="Recovered from an undeclared thread reference; name and track need review.",
            as_of=digest_date,
            slug=target,
        )
        counts["created"] += 1
        counts["recovered_threads"] += 1
        if verbose:
            print(
                f"  [Comprehension] {digest_date}: recovered undeclared thread "
                f"\"{target}\" ({len(pending)} item(s))"
            )
        resolved.extend((thread["slug"], item, assignment) for item, assignment in pending)

    for slug, item, assignment in resolved:
        if store.record_item(registry, slug, item, as_of=digest_date, note=assignment.get("note")):
            counts["filed"] += 1
        else:
            counts["duplicates"] += 1
        if assignment.get("promote"):
            counts["promoted"].append(item.get("item_key"))

    for merge in response.get("merges") or []:
        loser = new_slugs.get(str(merge.get("loser") or "")) or str(merge.get("loser") or "")
        winner = new_slugs.get(str(merge.get("winner") or "")) or str(merge.get("winner") or "")
        if store.merge_thread(registry, loser, winner, as_of=digest_date, why=merge.get("why")):
            counts["merged"] += 1

    counts["retired"] = len(store.retire_dormant(registry, digest_date, idle_days))

    if counts["unknown_threads"] and verbose:
        print(
            f"  [Comprehension] {digest_date}: dropped assignments to unresolvable threads "
            f"(new-thread cap reached): {', '.join(counts['unknown_threads'])}"
        )
    return counts


def _name_from_slug(slug):
    words = str(slug or "").replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Untitled thread"
