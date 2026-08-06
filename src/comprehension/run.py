"""CLI for the comprehension layer.

Run for one digest date:
    python3 -m src.comprehension.run --date 2026-08-04

Seed the registry from the archive (metadata only, no deep reads):
    python3 -m src.comprehension.run --backfill 2026-06-20..2026-08-03

Write the weekly synthesis essay (Opus) for the week ending on a date:
    python3 -m src.comprehension.run --weekly --date 2026-08-04

Inspect the registry without calling anything:
    python3 -m src.comprehension.run --report
"""

import argparse
import datetime
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.comprehension import (  # noqa: E402
    deepread, narrative, render, republish, sources, store, triage, weekly,
)
from src.comprehension.llm import (  # noqa: E402
    DEFAULT_DAILY_MODEL,
    DEFAULT_EFFORT,
    DEFAULT_WEEKLY_MODEL,
    ClaudeClient,
)
from src.comprehension.paths import (  # noqa: E402
    CACHE_FILE,
    CONFIG_FILE,
    DEFAULT_MARKS_DOWNLOAD_DIR,
    READER_PROFILE_FILE,
    THREADS_FILE,
)
from src.daily_digest import load_config  # noqa: E402

DEFAULT_SETTINGS = {
    "model": DEFAULT_DAILY_MODEL,
    "weekly_model": DEFAULT_WEEKLY_MODEL,
    "effort": DEFAULT_EFFORT,
    "max_new_threads": triage.DEFAULT_MAX_NEW_THREADS,
    "max_deep_reads": deepread.DEFAULT_MAX_DEEP_READS,
    "dormant_after_days": store.DEFAULT_DORMANT_AFTER_DAYS,
    "source_tracks": {},
}


def load_settings(path=CONFIG_FILE):
    config = store.load_json(path, {})
    settings = dict(DEFAULT_SETTINGS)
    settings.update((config.get("settings") or {}).get("comprehension") or {})
    return settings


def load_profile(path=READER_PROFILE_FILE):
    path = Path(path)
    if not path.exists():
        print(f"  [Comprehension] No reader profile at {path.name}; running without one.")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  [Comprehension] Could not read {path.name}: {e}")
        return ""


def parse_date_range(value):
    """'2026-06-20..2026-08-03' -> inclusive list of ISO date strings."""
    parts = [part.strip() for part in str(value).split("..")]
    if len(parts) != 2:
        raise ValueError("Backfill range must look like START..END (YYYY-MM-DD..YYYY-MM-DD).")
    try:
        start = datetime.date.fromisoformat(parts[0])
        end = datetime.date.fromisoformat(parts[1])
    except ValueError as exc:
        raise ValueError("Backfill dates must use YYYY-MM-DD format.") from exc
    if end < start:
        raise ValueError("Backfill END must not precede START.")
    return [
        (start + datetime.timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]


def process_date(
    digest_date,
    registry,
    client,
    settings,
    profile_text,
    narrate=True,
    deep_read=True,
    dry_run=False,
    digest_settings=None,
    verbose=True,
):
    track_map = settings.get("source_tracks")
    payload = sources.load_digest_json(digest_date)
    if payload:
        items, source = sources.items_from_digest_json(payload, track_map), "json"
    else:
        items, source = sources.items_from_markdown(digest_date, track_map=track_map), "markdown"
    if not items:
        print(f"  {digest_date}: no items found (checked JSON and Markdown) - skipped")
        return None

    result = triage.triage_day(
        items,
        registry,
        client,
        digest_date,
        profile_text=profile_text,
        max_new_threads=int(settings.get("max_new_threads", triage.DEFAULT_MAX_NEW_THREADS)),
        idle_days=int(settings.get("dormant_after_days", store.DEFAULT_DORMANT_AFTER_DAYS)),
        verbose=verbose,
    )
    result["items_by_key"] = {item["item_key"]: item for item in items}
    result["narratives"] = {}
    result["contexts"] = {}
    if narrate and result["ok"]:
        result["narratives"] = narrative.narrate_day(
            registry,
            digest_date,
            client,
            items_by_key=result["items_by_key"],
            profile_text=profile_text,
        )

    if deep_read and result["ok"] and result["promoted"]:
        result["contexts"] = deepread.context_for_items(
            result["promoted"],
            result["items_by_key"],
            client,
            thread_names=deepread.thread_names_for(registry, digest_date),
            max_deep_reads=int(settings.get("max_deep_reads", deepread.DEFAULT_MAX_DEEP_READS)),
            verbose=verbose,
        )
        # Context notes belong inline in the digest, but the digest was written
        # before this pass ran, so re-render it. Only possible from the JSON
        # contract; Markdown-sourced (backfilled) days are read-only here.
        if result["contexts"] and not dry_run:
            if payload:
                republish.rerender_digest(payload, result["contexts"], settings=digest_settings)
            elif verbose:
                print(
                    f"  [Comprehension] {digest_date}: no digest JSON, so Context notes "
                    "could not be folded into the digest."
                )

    status = "ok" if result["ok"] else "FAILED"
    extra = f", narrated {len(result['narratives'])}" if narrate else ""
    extra += f", context {len(result['contexts'])}" if deep_read else ""
    print(
        f"  {digest_date} [{source}] {result['items']:3d} items -> "
        f"filed {result['filed']:2d}, new {result['created']}, merged {result['merged']}, "
        f"dormant {result['retired']}, promote {len(result['promoted'])}{extra}  ({status})"
    )
    return result


def print_report(registry):
    summary = store.registry_summary(registry)
    print("\n  Thread registry")
    print(f"    threads: {summary['total']}  ({summary['by_status']})")
    print(f"    filed items: {summary['items']}")
    threads = sorted(
        registry.get("threads", []),
        key=lambda t: (len(t.get("items", [])), t.get("last_update") or ""),
        reverse=True,
    )
    for thread in threads:
        items = thread.get("items", [])
        span = ""
        if items:
            span = f" {items[0].get('digest_date')}..{items[-1].get('digest_date')}"
        flag = "" if thread.get("status") == "active" else f" [{thread.get('status')}]"
        print(
            f"    {len(items):3d} items{span}  {thread['slug']} "
            f"({thread.get('track')}) \"{thread.get('name')}\"{flag}"
        )
    singletons = [t for t in registry.get("threads", []) if len(t.get("items", [])) <= 1]
    if singletons:
        print(
            f"\n    churn signal: {len(singletons)}/{summary['total']} threads have <=1 item. "
            "Many single-item threads means the existing-thread bias is too weak."
        )


def run_weekly(args, settings, registry):
    """One Opus essay over the trailing week. Separate from the daily loop."""
    end_date = args.date or datetime.date.today().isoformat()
    client = ClaudeClient(
        model=args.model or weekly.default_weekly_model(settings),
        effort=args.effort or "high",
        cache={} if args.no_cache else None,
        cache_path=None if args.no_cache else CACHE_FILE,
    )
    print(f"\n  Weekly synthesis for the week ending {end_date}, model {client.model}")

    essay = weekly.synthesize_week(
        registry,
        narrative.load_narratives(),
        end_date,
        client,
        profile_text=load_profile(args.profile),
    )
    client.save_cache()
    if not essay:
        print("  No threads moved in that window (or the call failed); nothing written.")
        return 1
    print(f"  {essay['headline']}")
    if args.dry_run:
        print("  [dry-run] nothing written.")
        return 0

    weeklies = weekly.record_week(weekly.load_weeklies(), essay["week_end"], essay)
    weekly.save_weeklies(weeklies)
    paths = render.write_weekly(essay)
    archive = render.write_companion_archive_page()
    print(f"  Saved {paths['html_path']}")
    print(f"  Saved {archive}")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="File digest items into running comprehension threads.")
    parser.add_argument("--date", help="Digest date (YYYY-MM-DD) to process. Defaults to today's digest date.")
    parser.add_argument("--backfill", help="Inclusive digest-date range START..END to seed the registry.")
    parser.add_argument("--report", action="store_true", help="Print the registry and exit without calling the API.")
    parser.add_argument(
        "--weekly", action="store_true",
        help="Write the weekly synthesis essay for the week ending on --date (Opus).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write threads.json, narratives, or pages.")
    parser.add_argument(
        "--narrate", dest="narrate", action="store_true", default=None,
        help="Write narratives and companion pages (default for --date, off for --backfill).",
    )
    parser.add_argument(
        "--no-narrate", dest="narrate", action="store_false",
        help="Triage only: file items into threads without narrating or rendering.",
    )
    parser.add_argument(
        "--no-deep-read", dest="deep_read", action="store_false", default=None,
        help="Skip fetching full text and writing Context notes into the digest.",
    )
    parser.add_argument("--max-deep-reads", type=int, help="Cap on items fetched and read in full per day.")
    parser.add_argument("--model", help="Override the model id for this run.")
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), help="Override effort.")
    parser.add_argument("--max-new-threads", type=int, help="Per-run cap on newly opened threads.")
    parser.add_argument("--threads-file", default=str(THREADS_FILE), help="Path to the thread registry.")
    parser.add_argument("--profile", default=str(READER_PROFILE_FILE), help="Path to the reader profile.")
    parser.add_argument("--no-cache", action="store_true", help="Ignore and do not write the LLM result cache.")
    parser.add_argument("--no-harvest", action="store_true", help="Skip absorbing exported reading marks.")
    parser.add_argument(
        "--downloads-dir", default=str(DEFAULT_MARKS_DOWNLOAD_DIR),
        help="Where the digest page's 'Save marks' download lands.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    settings = load_settings()
    registry = store.load_threads(args.threads_file)

    if args.report:
        print_report(registry)
        return 0

    if args.weekly:
        return run_weekly(args, settings, registry)

    if args.backfill:
        try:
            dates = parse_date_range(args.backfill)
        except ValueError as e:
            print(f"  [Comprehension] {e}")
            return 2
    elif args.date:
        dates = [args.date]
    else:
        dates = [(datetime.date.today()).isoformat()]

    if args.model:
        settings["model"] = args.model
    if args.effort:
        settings["effort"] = args.effort
    if args.max_new_threads is not None:
        settings["max_new_threads"] = args.max_new_threads
    if args.max_deep_reads is not None:
        settings["max_deep_reads"] = args.max_deep_reads

    client = ClaudeClient(
        model=settings["model"],
        effort=settings["effort"],
        cache={} if args.no_cache else None,
        cache_path=None if args.no_cache else CACHE_FILE,
    )
    profile_text = load_profile(args.profile)

    # Backfill seeds thread history from the archive; narrating and deep-reading
    # 45 past days is neither useful nor cheap, so both are opt-in there and
    # default-on for a single date.
    narrate = args.narrate if args.narrate is not None else not args.backfill
    deep_read = args.deep_read if args.deep_read is not None else not args.backfill
    digest_settings = load_config().get("settings", {})

    # Marks exported from the digest page land in ~/Downloads; absorb them before
    # the run so this pass reflects what already landed. No files is a no-op.
    if not args.no_harvest:
        harvested = store.harvest_marks(downloads_dir=args.downloads_dir)
        if harvested["files"]:
            print(f"  Harvested {harvested['marks']} mark(s) from {harvested['files']} file(s).")
    altitude = store.marks_altitude_note(store.load_marks(), registry)
    if altitude:
        profile_text = f"{profile_text}\n\n{altitude}".strip()

    print(
        f"\n  Comprehension: {len(dates)} date(s), model {client.model}, "
        f"effort {client.effort}, narrate {narrate}, deep-read {deep_read}"
    )
    processed = 0
    narratives = narrative.load_narratives()
    rendered = []
    for digest_date in dates:
        result = process_date(
            digest_date, registry, client, settings, profile_text,
            narrate=narrate, deep_read=deep_read, dry_run=args.dry_run,
            digest_settings=digest_settings,
        )
        if not result:
            continue
        processed += 1
        if narrate and result["narratives"]:
            narrative.record_narratives(narratives, digest_date, result["narratives"])
            if not args.dry_run:
                paths = render.write_companion(
                    digest_date, registry, result["narratives"], result["items_by_key"]
                )
                rendered.append(paths["html_path"])

    client.save_cache()
    print(
        f"\n  Processed {processed}/{len(dates)} dates · "
        f"{client.calls} API calls, {client.cache_hits} cache hits"
    )

    if args.dry_run:
        print("  [dry-run] nothing written.")
    else:
        store.save_threads(registry, args.threads_file, as_of=dates[-1])
        print(f"  Saved {args.threads_file}")
        if narrate:
            narrative.save_narratives(narratives)
            archive = render.write_companion_archive_page()
            for path in rendered:
                print(f"  Saved {path}")
            print(f"  Saved {archive}")

    print_report(registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
