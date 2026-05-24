import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.reddit import reddit_cleanup


class RedditCleanupTests(unittest.TestCase):
    def test_mapping_counts_and_feed_groups_match_approved_file(self):
        rows = reddit_cleanup.load_mapping(reddit_cleanup.DEFAULT_MAPPING_FILE)
        plan = reddit_cleanup.build_cleanup_plan(rows, username="example_user")

        self.assertEqual(len(rows), 749)
        self.assertEqual(len(plan.prune_rows), 240)
        self.assertEqual(len(plan.custom_feed_rows), 97)
        self.assertEqual(set(plan.feed_groups), {
            "AI & Productivity",
            "Business",
            "Energy",
            "Lifestyle",
            "Music & Writing",
            "Politics",
            "Technology",
        })

    def test_digest_maybe_values_are_eligible_but_no_is_not(self):
        rows = [
            {"subreddit": "a", "category": "AI", "PRUNE?": "No", "DIGEST?": "Yes", "feed_group": "AI & Productivity"},
            {"subreddit": "b", "category": "AI", "PRUNE?": "No", "DIGEST?": "Maybe/Yes", "feed_group": "AI & Productivity"},
            {"subreddit": "c", "category": "AI", "PRUNE?": "No", "DIGEST?": "Maybe/No", "feed_group": "AI & Productivity"},
            {"subreddit": "d", "category": "AI", "PRUNE?": "No", "DIGEST?": "No", "feed_group": "AI & Productivity"},
            {"subreddit": "e", "category": "AI", "PRUNE?": "Yes", "DIGEST?": "Yes", "feed_group": "AI & Productivity"},
        ]
        plan = reddit_cleanup.build_cleanup_plan(rows, username="example_user")

        self.assertEqual([row["subreddit"] for row in plan.custom_feed_rows], ["a", "b", "c"])
        self.assertEqual([row["subreddit"] for row in plan.prune_rows], ["e"])

    def test_rss_links_use_multireddit_slug_and_expected_sorts(self):
        rows = [
            {"subreddit": "ClaudeAI", "category": "AI", "PRUNE?": "No", "DIGEST?": "Yes", "feed_group": "AI & Productivity"},
        ]
        plan = reddit_cleanup.build_cleanup_plan(rows, username="michelle")

        self.assertEqual(plan.feed_groups["AI & Productivity"].slug, "ai_and_productivity")
        self.assertEqual(plan.feed_groups["AI & Productivity"].rss_links, {
            "top_day": "https://www.reddit.com/user/michelle/m/ai_and_productivity/top/.rss?t=day",
            "best_day": "https://www.reddit.com/user/michelle/m/ai_and_productivity/best/.rss?t=day",
            "hot_day": "https://www.reddit.com/user/michelle/m/ai_and_productivity/hot/.rss?t=day",
        })

    def test_write_action_plan_outputs_csv_and_markdown(self):
        rows = [
            {"subreddit": "ClaudeAI", "category": "AI", "PRUNE?": "No", "DIGEST?": "Yes", "feed_group": "AI & Productivity"},
            {"subreddit": "funny", "category": "Humor", "PRUNE?": "Yes", "DIGEST?": "No", "feed_group": "Lifestyle"},
        ]
        plan = reddit_cleanup.build_cleanup_plan(rows, username="example_user")

        with tempfile.TemporaryDirectory() as tmp:
            output = reddit_cleanup.write_action_plan(plan, Path(tmp), "2026-05-03", executed=False)
            self.assertTrue(output.markdown_path.exists())
            self.assertTrue(output.prune_csv_path.exists())
            self.assertTrue(output.custom_feed_csv_path.exists())
            self.assertTrue(output.rss_csv_path.exists())

            with output.rss_csv_path.open(encoding="utf-8", newline="") as f:
                rss_rows = list(csv.DictReader(f))
            self.assertEqual(len(rss_rows), 3)
            self.assertEqual({row["sort"] for row in rss_rows}, {"top_day", "best_day", "hot_day"})

    def test_execute_without_oauth_fails_before_actions(self):
        rows = [
            {"subreddit": "funny", "category": "Humor", "PRUNE?": "Yes", "DIGEST?": "No", "feed_group": "Lifestyle"},
        ]
        plan = reddit_cleanup.build_cleanup_plan(rows, username="example_user")
        clean_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_REFRESH_TOKEN"}
        }

        with patch.dict(os.environ, clean_env, clear=True):
            with self.assertRaises(reddit_cleanup.RedditAuthError):
                reddit_cleanup.execute_plan(plan, execute_prune_flag=True, execute_custom_feeds_flag=False)


if __name__ == "__main__":
    unittest.main()
