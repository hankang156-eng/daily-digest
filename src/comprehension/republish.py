"""Re-render the digest with `Context:` notes folded in.

The comprehension pass runs after the digest has already been written, so the
digest for a given day cannot contain that day's Context notes at render time.
Rather than move comprehension into the digest's critical path, the digest writes
normally and this module re-renders it afterwards from the JSON contract.

If comprehension fails or is skipped, nothing here runs and the digest that was
already written stands untouched.
"""

import datetime

from src.daily_digest import (
    DAILY_HTML_DIR,
    DAILY_MD_DIR,
    DEFAULT_CONFIG,
    REPO_ROOT,
    digest_archive_entries,
    generate_html,
    generate_markdown,
)

# build_sections re-splits "blogs" into research + blogs by source name, so
# concatenating them back in this order reproduces the original ordering exactly.
RESEARCH_FIRST = ("research", "blogs")


def rehydrate_data(payload):
    """Rebuild the in-memory `data` dict the digest renderers expect."""
    sections = payload.get("sections") or {}
    blogs = []
    for group in RESEARCH_FIRST:
        blogs.extend(sections.get(group) or [])
    return {
        "hn": list(sections.get("hn") or []),
        "nyt_wsj": list(sections.get("nyt_wsj") or []),
        "blogs": blogs,
        "reddit": list(sections.get("reddit") or []),
        "linkedin": list(sections.get("linkedin") or []),
        "nyt_note": payload.get("nyt_note", ""),
    }


def inject_context(data, contexts):
    """Attach context notes to their items. Returns the number attached."""
    if not contexts:
        return 0
    attached = 0
    for group in ("hn", "nyt_wsj", "blogs", "reddit", "linkedin"):
        for item in data.get(group) or []:
            note = contexts.get(item.get("item_key"))
            if note:
                item["context_note"] = note
                attached += 1
    return attached


def is_latest_digest(digest_date, daily_html_dir=DAILY_HTML_DIR):
    """True when this date is the newest digest on disk, i.e. what index.html mirrors."""
    entries = digest_archive_entries(daily_html_dir)
    return bool(entries) and entries[0][0] == str(digest_date)


def rerender_digest(
    payload,
    contexts,
    settings=None,
    daily_html_dir=DAILY_HTML_DIR,
    daily_md_dir=DAILY_MD_DIR,
    index_path=None,
    verbose=True,
):
    """Re-write this day's digest HTML/MD with Context notes included.

    Also refreshes index.html, but only when this day is the newest digest on
    disk - re-running a past date must not clobber the current index.
    """
    settings = settings or DEFAULT_CONFIG["settings"]
    digest_date = payload.get("digest_date")
    try:
        content_date = datetime.date.fromisoformat(payload["content_date"])
    except (KeyError, TypeError, ValueError):
        if verbose:
            print("  [Comprehension] Digest JSON has no usable content_date; skipping re-render.")
        return {}

    data = rehydrate_data(payload)
    attached = inject_context(data, contexts)
    if not attached:
        return {}

    written = {}
    html_path = daily_html_dir / f"digest_{digest_date}.html"
    md_path = daily_md_dir / f"digest_{digest_date}.md"
    try:
        daily_html_dir.mkdir(parents=True, exist_ok=True)
        daily_md_dir.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            generate_html(content_date, data, settings, archive_href="../../digest_archive.html"),
            encoding="utf-8",
        )
        md_path.write_text(generate_markdown(content_date, data, settings), encoding="utf-8")
        written["html_path"] = html_path
        written["md_path"] = md_path
    except Exception as e:
        if verbose:
            print(f"  [Comprehension] Could not re-render the digest: {e}")
        return written

    if is_latest_digest(digest_date, daily_html_dir):
        index_path = index_path or (REPO_ROOT / "index.html")
        try:
            index_path.write_text(
                generate_html(content_date, data, settings, archive_href="digest_archive.html"),
                encoding="utf-8",
            )
            written["index_path"] = index_path
        except Exception as e:
            if verbose:
                print(f"  [Comprehension] Could not refresh index.html: {e}")

    if verbose:
        print(f"  [Comprehension] Re-rendered the digest with {attached} Context note(s).")
    written["attached"] = attached
    return written
