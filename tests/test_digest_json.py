import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from src import daily_digest


def _sample_data():
    """One item per section, carrying the fields the JSON contract must preserve."""
    return {
        "hn": [{
            "title": "Example HN story",
            "url": "https://example.com/story",
            "hn_url": "https://news.ycombinator.com/item?id=123",
            "hn_companion_url": "https://app.hncompanion.com/item?id=123",
            "discussion_overview": "Commenters compare trade-offs.",
            "object_id": "123",
            "score": 100,
            "comments": 25,
            "author": "someone",
            "outlet": "HN",
            "source": "HackerNews",
            "category": "tech",
            "section": None,
            "date": dt.date(2026, 5, 23),
            "word_count": 1200,
            "reading_time_minutes": 5,
        }],
        "nyt_wsj": [{
            "title": "Example NYT story",
            "url": "https://nytimes.com/2026/05/23/business/example.html",
            "source": "NYT Business",
            "outlet": "NYT",
            "section": "Business",
            "topic": "Business",
            "topic_tag": "Business",
            "category": "news",
            "date": "2026-05-23",
            "summary": "Feed summary.",
            "abstract": "RSS abstract.",
            "article_overview": "Gemini overview text.",
            "overview_model": "Gemini 3.1 Flash-Lite",
            "score": 87.5,
            "reason": "Cross-section prominence.",
            "reading_mode": "Read deeply",
            "word_count": 1800,
            "reading_time_minutes": 8,
        }],
        "blogs": [
            {
                "title": "Example research paper",
                "url": "https://ide.mit.edu/example",
                "source": "MIT IDE",
                "outlet": "",
                "category": "research",
                "date": dt.date(2026, 5, 22),
                "date_confidence": "high",
                "summary": "Research summary.",
                "article_overview": "Research overview text.",
                "score": 71.0,
                "reason": "Research signal.",
                "reading_mode": "Read deeply",
            },
            {
                "title": "Example blog post",
                "url": "https://simonwillison.net/example",
                "source": "Simon Willison",
                "outlet": "",
                "category": "tech",
                "date": dt.date(2026, 5, 23),
                "summary": "Blog summary.",
                "article_overview": "Blog overview text.",
                "score": 64.0,
                "reading_mode": "Skim",
            },
        ],
        "reddit": [{
            "title": "Example reddit post",
            "url": "https://reddit.com/r/ClaudeAI/comments/abc",
            "source": "r/ClaudeAI",
            "outlet": "Reddit",
            "category": "social",
            "date": dt.date(2026, 5, 23),
            "summary": "Post body.",
            "reddit_url": "https://reddit.com/r/ClaudeAI/comments/abc",
            "reddit_author": "poster",
            "reddit_id": "abc",
            "bot_tldr": "Bot summary.",
        }],
        "linkedin": [],
        "nyt_note": "",
    }


class DigestJsonContractTests(unittest.TestCase):
    def test_digest_date_is_the_day_after_the_content_date(self):
        payload = daily_digest.build_digest_json(dt.date(2026, 5, 23), _sample_data())

        self.assertEqual(payload["content_date"], "2026-05-23")
        self.assertEqual(payload["digest_date"], "2026-05-24")
        self.assertEqual(payload["schema_version"], daily_digest.DIGEST_JSON_VERSION)

    def test_sections_mirror_build_sections_including_the_research_split(self):
        payload = daily_digest.build_digest_json(dt.date(2026, 5, 23), _sample_data())

        self.assertEqual(set(payload["sections"]), set(daily_digest.DIGEST_JSON_GROUPS))
        self.assertEqual(
            [item["title"] for item in payload["sections"]["research"]],
            ["Example research paper"],
        )
        self.assertEqual(
            [item["title"] for item in payload["sections"]["blogs"]],
            ["Example blog post"],
        )

    def test_contract_preserves_the_fields_the_archive_drops(self):
        payload = daily_digest.build_digest_json(dt.date(2026, 5, 23), _sample_data())

        nyt = payload["sections"]["nyt_wsj"][0]
        self.assertEqual(nyt["score"], 87.5)
        self.assertEqual(nyt["reason"], "Cross-section prominence.")
        self.assertEqual(nyt["reading_mode"], "Read deeply")
        self.assertEqual(nyt["abstract"], "RSS abstract.")
        self.assertEqual(nyt["article_overview"], "Gemini overview text.")
        self.assertEqual(nyt["word_count"], 1800)
        self.assertEqual(nyt["reading_time_minutes"], 8)
        self.assertEqual(nyt["topic_tag"], "Business")

        hn = payload["sections"]["hn"][0]
        self.assertEqual(hn["discussion_overview"], "Commenters compare trade-offs.")
        self.assertEqual(hn["comments"], 25)

        reddit = payload["sections"]["reddit"][0]
        self.assertEqual(reddit["bot_tldr"], "Bot summary.")

    def test_every_item_carries_a_stable_key_group_and_topic(self):
        payload = daily_digest.build_digest_json(dt.date(2026, 5, 23), _sample_data())

        for group, items in payload["sections"].items():
            for item in items:
                self.assertEqual(item["group"], group)
                self.assertEqual(
                    item["item_key"],
                    daily_digest.article_key({"url": item["url"], "title": item["title"]}),
                )
                self.assertTrue(item["topic"])

    def test_dates_are_serialized_as_iso_strings(self):
        payload = daily_digest.build_digest_json(dt.date(2026, 5, 23), _sample_data())

        self.assertEqual(payload["sections"]["hn"][0]["date"], "2026-05-23")
        self.assertEqual(payload["sections"]["nyt_wsj"][0]["date"], "2026-05-23")
        self.assertEqual(payload["sections"]["research"][0]["date"], "2026-05-22")

    def test_payload_round_trips_through_json(self):
        payload = daily_digest.build_digest_json(dt.date(2026, 5, 23), _sample_data())

        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_dump_writes_a_readable_file_at_the_digest_date_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "digest_2026-05-24.json"

            written = daily_digest.dump_digest_json(
                dt.date(2026, 5, 23), _sample_data(), path=path
            )

            self.assertEqual(written, path)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["digest_date"], "2026-05-24")
        self.assertEqual(payload["sections"]["nyt_wsj"][0]["score"], 87.5)

    def test_empty_sections_are_present_and_empty(self):
        payload = daily_digest.build_digest_json(
            dt.date(2026, 5, 23),
            {"hn": [], "nyt_wsj": [], "blogs": [], "linkedin": []},
        )

        for group in daily_digest.DIGEST_JSON_GROUPS:
            self.assertEqual(payload["sections"][group], [])

    def test_json_dump_is_on_by_default_and_can_be_skipped(self):
        self.assertFalse(daily_digest.parse_args([]).no_json)
        self.assertTrue(daily_digest.parse_args(["--no-json"]).no_json)


class ArchivePreservationTests(unittest.TestCase):
    """Regression: running the digest where the gitignored archive JSON is absent
    (a git worktree) rebuilt a 100-day table from one day and wiped the history."""

    def test_a_rebuild_that_would_discard_most_of_the_table_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "archive.md"
            md_path.write_text("a hundred days of history\n" * 200, encoding="utf-8")

            self.assertTrue(daily_digest._archive_would_shrink(md_path, "one day only\n"))

    def test_a_rebuild_that_grows_or_holds_the_table_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "archive.md"
            existing = "a hundred days of history\n" * 200
            md_path.write_text(existing, encoding="utf-8")

            self.assertFalse(daily_digest._archive_would_shrink(md_path, existing))
            self.assertFalse(daily_digest._archive_would_shrink(md_path, existing + "one more day\n"))
            # A modest shrink is normal churn, not data loss.
            self.assertFalse(daily_digest._archive_would_shrink(md_path, existing[: int(len(existing) * 0.9)]))

    def test_a_fresh_checkout_with_no_table_yet_is_allowed_to_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(daily_digest._archive_would_shrink(Path(tmp) / "absent.md", "first day\n"))

    def test_the_real_archives_survive_a_rebuild_from_this_worktrees_partial_state(self):
        """The exact regression: a worktree's thin archive JSON must not wipe the table."""
        for md_path, table in (
            (daily_digest.HN_DATA_DIR / "hn_archive.md", daily_digest._hn_md_table),
            (daily_digest.DIGEST_ARCHIVE_DIR / "dd_archive.md", daily_digest._dd_md_table),
        ):
            if md_path.exists():
                self.assertTrue(daily_digest._archive_would_shrink(md_path, table({})), md_path.name)


class ReadingMarksTests(unittest.TestCase):
    """The digest-side half of the feedback loop.

    Marks are feedback on an *explanation*, so the digest offers them only on the
    few items carrying a Context note. Offering them on all ~67 items made the
    target ambiguous and the loop unusable; threads on the companion page are the
    primary surface.
    """

    def _html(self, data=None):
        return daily_digest.generate_html(dt.date(2026, 5, 23), data or _sample_data())

    def test_every_item_carries_the_same_key_the_comprehension_layer_files_by(self):
        html = self._html()

        for group in ("hn", "nyt_wsj", "blogs", "reddit"):
            for item in _sample_data()[group]:
                key = daily_digest.article_key(item)
                self.assertIn(f'data-item-key="{key}"', html)

    def test_items_without_a_context_note_offer_no_marks(self):
        self.assertNotIn('class="marks"', self._html())

    def test_marks_appear_exactly_on_items_carrying_a_context_note(self):
        data = _sample_data()
        data["hn"][0]["context_note"] = "The background it assumes."
        data["blogs"][1]["context_note"] = "More background."

        html = self._html(data)

        self.assertEqual(html.count('class="marks"'), 2)
        for item in (data["hn"][0], data["blogs"][1]):
            self.assertIn(f'data-mark-key="{daily_digest.article_key(item)}"', html)
        for value in ("knew-this", "useful", "over-my-head"):
            self.assertIn(f'data-mark="{value}"', html)
        self.assertIn('aria-label="How this landed"', html)

    def test_the_page_exposes_its_digest_date_for_the_export_filename(self):
        html = self._html()

        self.assertIn('data-digest-date="2026-05-24"', html)
        self.assertIn('id="saveMarks"', html)
        self.assertIn("dd-marks-", html)

    def test_the_script_binds_by_mark_key_so_it_serves_both_surfaces(self):
        html = self._html()

        # The companion page has .thread, not .article, so the selector must not
        # depend on an .article ancestor.
        self.assertIn(".marks[data-mark-key]", html)
        self.assertIn("closest('.article, .thread')", html)

    def test_marks_are_persisted_under_a_stable_localstorage_key(self):
        html = self._html()

        self.assertIn("var KEY = 'dd-marks';", html)
        # Reuses the existing font/theme persistence pattern rather than a new one.
        self.assertIn("localStorage.setItem('dd-font'", html)


if __name__ == "__main__":
    unittest.main()
