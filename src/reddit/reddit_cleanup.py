#!/usr/bin/env python3
"""Safe Reddit prune and custom-feed planner/executor."""

from __future__ import annotations

import argparse
import base64
import csv
import dataclasses
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING_FILE = REPO_ROOT / "data/reddit/cleanup/final_mapping/processed_reddit_subscriptions_feed_categories_remapped_2026-05-03.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/reddit/cleanup/action_plans"
REDDIT_API_BASE = "https://oauth.reddit.com"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
USER_AGENT = "script:daily-digest-cleanup:v0.1.0 (by /u/miss_comte)"
RATE_LIMIT_FLOOR = 5.0
RATE_LIMIT_PAUSE_SECONDS = 2.0
DIGEST_ELIGIBLE = {"yes", "maybe/yes", "maybe/no"}


@dataclasses.dataclass
class FeedGroupPlan:
    name: str
    slug: str
    subreddits: list[str]
    rss_links: dict[str, str]


@dataclasses.dataclass
class CleanupPlan:
    username: str
    all_rows: list[dict[str, str]]
    prune_rows: list[dict[str, str]]
    custom_feed_rows: list[dict[str, str]]
    feed_groups: dict[str, FeedGroupPlan]


@dataclasses.dataclass
class ActionPlanOutput:
    markdown_path: Path
    prune_csv_path: Path
    custom_feed_csv_path: Path
    rss_csv_path: Path
    execution_log_path: Path


class RedditAuthError(RuntimeError):
    pass


def normalize(value: str | None) -> str:
    return (value or "").strip()


def is_prune_yes(value: str | None) -> bool:
    return normalize(value).lower() == "yes"


def is_digest_eligible(value: str | None) -> bool:
    return normalize(value).lower() in DIGEST_ELIGIBLE


def slugify_feed_group(value: str) -> str:
    slug = normalize(value).lower().replace("&", "and")
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug[:50] or "reddit_feed"


def load_mapping(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    required = {"subreddit", "category", "PRUNE?", "DIGEST?", "feed_group"}
    missing = required - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"Mapping file is missing required columns: {', '.join(sorted(missing))}")
    return rows


def rss_links(username: str, feed_slug: str) -> dict[str, str]:
    base = f"https://www.reddit.com/user/{username}/m/{feed_slug}"
    return {
        "top_day": f"{base}/top/.rss?t=day",
        "best_day": f"{base}/best/.rss?t=day",
        "hot_day": f"{base}/hot/.rss?t=day",
    }


def build_cleanup_plan(rows: list[dict[str, str]], username: str) -> CleanupPlan:
    normalized_rows = [{key: normalize(value) for key, value in row.items()} for row in rows]
    prune_rows = [row for row in normalized_rows if is_prune_yes(row.get("PRUNE?"))]
    custom_feed_rows = [
        row for row in normalized_rows
        if not is_prune_yes(row.get("PRUNE?")) and is_digest_eligible(row.get("DIGEST?"))
    ]
    grouped: dict[str, list[str]] = {}
    for row in custom_feed_rows:
        grouped.setdefault(row["feed_group"], []).append(row["subreddit"])

    feed_groups = {
        name: FeedGroupPlan(
            name=name,
            slug=slugify_feed_group(name),
            subreddits=sorted(set(subreddits), key=str.lower),
            rss_links=rss_links(username, slugify_feed_group(name)),
        )
        for name, subreddits in sorted(grouped.items())
    }
    return CleanupPlan(
        username=username,
        all_rows=normalized_rows,
        prune_rows=prune_rows,
        custom_feed_rows=custom_feed_rows,
        feed_groups=feed_groups,
    )


def write_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_action_plan(plan: CleanupPlan, output_dir: Path, date_tag: str | None = None, executed: bool = False) -> ActionPlanOutput:
    date_tag = date_tag or dt.date.today().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"reddit_cleanup_plan_{date_tag}"
    markdown_path = output_dir / f"{prefix}.md"
    prune_csv_path = output_dir / f"{prefix}_prune.csv"
    custom_feed_csv_path = output_dir / f"{prefix}_custom_feeds.csv"
    rss_csv_path = output_dir / f"{prefix}_rss_links.csv"
    execution_log_path = output_dir / f"{prefix}_execution_log.json"

    write_rows(prune_csv_path, plan.prune_rows, ["subreddit", "category", "PRUNE?", "DIGEST?", "feed_group", "saved_posts_count", "priority_tier"])

    custom_rows = []
    for group in plan.feed_groups.values():
        for subreddit in group.subreddits:
            custom_rows.append({"feed_group": group.name, "feed_slug": group.slug, "subreddit": subreddit})
    write_rows(custom_feed_csv_path, custom_rows, ["feed_group", "feed_slug", "subreddit"])

    rss_rows = []
    for group in plan.feed_groups.values():
        for sort_name, url in group.rss_links.items():
            rss_rows.append({"feed_group": group.name, "feed_slug": group.slug, "sort": sort_name, "rss_url": url})
    write_rows(rss_csv_path, rss_rows, ["feed_group", "feed_slug", "sort", "rss_url"])

    lines = [
        f"# Reddit Cleanup Plan - {date_tag}",
        "",
        f"- Mode: {'executed' if executed else 'dry run'}",
        f"- Reddit username for links/actions: `{plan.username}`",
        f"- Mapping rows: {len(plan.all_rows):,}",
        f"- Subreddits marked for prune: {len(plan.prune_rows):,}",
        f"- Subreddits marked for custom feeds: {len(plan.custom_feed_rows):,}",
        f"- Custom feed groups: {len(plan.feed_groups):,}",
        "",
        "## Custom Feeds",
        "",
        "| Feed group | Feed slug | Subreddits | RSS links |",
        "|---|---|---:|---|",
    ]
    for group in plan.feed_groups.values():
        links = "<br>".join(f"{name}: {url}" for name, url in group.rss_links.items())
        lines.append(f"| {group.name} | `{group.slug}` | {len(group.subreddits)} | {links} |")
    lines += [
        "",
        "## Safety",
        "",
        "This plan does not mutate Reddit unless rerun with `--execute` and at least one of `--execute-prune` or `--execute-custom-feeds`.",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not execution_log_path.exists():
        execution_log_path.write_text(json.dumps({"executed": executed, "actions": []}, indent=2), encoding="utf-8")

    return ActionPlanOutput(markdown_path, prune_csv_path, custom_feed_csv_path, rss_csv_path, execution_log_path)


def reddit_oauth_session() -> requests.Session:
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    refresh_token = os.environ.get("REDDIT_REFRESH_TOKEN")
    missing = [name for name, value in {
        "REDDIT_CLIENT_ID": client_id,
        "REDDIT_CLIENT_SECRET": client_secret,
        "REDDIT_REFRESH_TOKEN": refresh_token,
    }.items() if not value]
    if missing:
        raise RedditAuthError(f"Missing Reddit OAuth environment variables: {', '.join(missing)}")

    assert client_id is not None
    assert client_secret is not None
    assert refresh_token is not None
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    response = requests.post(
        REDDIT_TOKEN_URL,
        headers={"Authorization": f"Basic {auth}", "User-Agent": USER_AGENT},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=20,
    )
    if response.status_code != 200:
        raise RedditAuthError(f"Reddit token refresh failed: HTTP {response.status_code} {response.text[:300]}")
    token = response.json().get("access_token")
    if not token:
        raise RedditAuthError("Reddit token refresh response did not include access_token")
    session = requests.Session()
    session.headers.update({"Authorization": f"bearer {token}", "User-Agent": USER_AGENT})
    return session


def oauth_env_available() -> bool:
    return all(os.environ.get(name) for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_REFRESH_TOKEN"))


def authenticated_username(session: requests.Session) -> str:
    response = session.get(f"{REDDIT_API_BASE}/api/v1/me", timeout=20)
    maybe_pause_for_rate_limit(response)
    if response.status_code != 200:
        raise RedditAuthError(f"Could not fetch Reddit identity: HTTP {response.status_code} {response.text[:300]}")
    username = response.json().get("name")
    if not username:
        raise RedditAuthError("Reddit identity response did not include username")
    return username


def maybe_pause_for_rate_limit(response: requests.Response) -> None:
    try:
        remaining = float(response.headers.get("X-Ratelimit-Remaining", "999"))
        reset = float(response.headers.get("X-Ratelimit-Reset", "0"))
    except ValueError:
        return
    if remaining <= RATE_LIMIT_FLOOR:
        time.sleep(max(RATE_LIMIT_PAUSE_SECONDS, min(reset, 30.0)))


def reddit_request(session: requests.Session, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, f"{REDDIT_API_BASE}{path}", timeout=20, **kwargs)
    maybe_pause_for_rate_limit(response)
    if response.status_code in {401, 403, 429} or response.status_code >= 500:
        raise RuntimeError(f"Reddit API {method} {path} failed: HTTP {response.status_code} {response.text[:300]}")
    if response.status_code not in {200, 201, 202, 204}:
        raise RuntimeError(f"Reddit API {method} {path} returned HTTP {response.status_code}: {response.text[:300]}")
    if not response.text:
        return {}
    return response.json()


def execute_prune(session: requests.Session, plan: CleanupPlan) -> list[dict[str, Any]]:
    actions = []
    for row in plan.prune_rows:
        subreddit = row["subreddit"]
        reddit_request(session, "POST", "/api/subscribe", data={"action": "unsub", "sr_name": subreddit, "api_type": "json"})
        actions.append({"action": "unsubscribe", "subreddit": subreddit, "status": "ok"})
        time.sleep(0.25)
    return actions


def execute_custom_feeds(session: requests.Session, plan: CleanupPlan) -> list[dict[str, Any]]:
    actions = []
    for group in plan.feed_groups.values():
        multipath = f"/user/{plan.username}/m/{group.slug}"
        model = {
            "description_md": f"Generated from daily_digest approved Reddit mapping for {group.name}.",
            "display_name": group.name[:50],
            "subreddits": [{"name": subreddit} for subreddit in group.subreddits],
            "visibility": "public",
        }
        reddit_request(session, "PUT", f"/api/multi{multipath}", data={"model": json.dumps(model), "api_type": "json"})
        actions.append({"action": "upsert_custom_feed", "feed_group": group.name, "feed_slug": group.slug, "subreddits": len(group.subreddits), "status": "ok"})
        time.sleep(0.5)
    return actions


def execute_plan(plan: CleanupPlan, execute_prune_flag: bool, execute_custom_feeds_flag: bool) -> list[dict[str, Any]]:
    if not execute_prune_flag and not execute_custom_feeds_flag:
        raise ValueError("--execute requires --execute-prune and/or --execute-custom-feeds")
    session = reddit_oauth_session()
    username = authenticated_username(session)
    if username.lower() != plan.username.lower():
        plan.username = username
    actions: list[dict[str, Any]] = []
    if execute_prune_flag:
        actions.extend(execute_prune(session, plan))
    if execute_custom_feeds_flag:
        actions.extend(execute_custom_feeds(session, plan))
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan or execute safe Reddit cleanup/custom-feed actions.")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--username", default="", help="Dry-run username for RSS links. Execution always uses OAuth /me.")
    parser.add_argument("--execute", action="store_true", help="Allow account-changing Reddit API calls.")
    parser.add_argument("--execute-prune", action="store_true", help="When used with --execute, unsubscribe PRUNE?=Yes subreddits.")
    parser.add_argument("--execute-custom-feeds", action="store_true", help="When used with --execute, create/update custom feeds.")
    args = parser.parse_args()

    try:
        rows = load_mapping(args.mapping)
        username = args.username or "REDDIT_USERNAME"
        if args.execute or (not args.username and oauth_env_available()):
            session = reddit_oauth_session()
            username = authenticated_username(session)
        plan = build_cleanup_plan(rows, username=username)
        output = write_action_plan(plan, args.output_dir, executed=args.execute)

        actions: list[dict[str, Any]] = []
        if args.execute:
            actions = execute_plan(plan, args.execute_prune, args.execute_custom_feeds)
            output.execution_log_path.write_text(json.dumps({"executed": True, "username": plan.username, "actions": actions}, indent=2), encoding="utf-8")
    except (RedditAuthError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None

    print(f"Mapping rows: {len(plan.all_rows)}")
    print(f"Prune candidates: {len(plan.prune_rows)}")
    print(f"Custom-feed candidates: {len(plan.custom_feed_rows)}")
    print(f"Custom feed groups: {len(plan.feed_groups)}")
    print(f"Plan: {output.markdown_path}")
    print(f"RSS links: {output.rss_csv_path}")
    if args.execute:
        print(f"Executed actions: {len(actions)}")


if __name__ == "__main__":
    main()
