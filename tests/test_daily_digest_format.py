import datetime as dt
import tempfile
import unittest
from pathlib import Path

from src import daily_digest
from src.rankers import blog_reading_ranker


class DailyDigestFormatTests(unittest.TestCase):
    def test_generated_html_links_to_past_digests_page(self):
        html = daily_digest.generate_html(
            dt.date(2026, 5, 24),
            {"hn": [], "nyt_wsj": [], "blogs": [], "linkedin": []},
        )

        self.assertIn('href="digest_archive.html"', html)
        self.assertIn("Read past daily digests", html)

    def test_digest_archive_entries_are_descending_with_weekday_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            daily_html_dir = Path(tmp)
            (daily_html_dir / "digest_2026-05-22.html").write_text("older", encoding="utf-8")
            (daily_html_dir / "digest_2026-05-24.html").write_text("newer", encoding="utf-8")
            (daily_html_dir / "digest_not-a-date.html").write_text("ignored", encoding="utf-8")

            entries = daily_digest.digest_archive_entries(daily_html_dir)

        self.assertEqual(
            entries,
            [
                ("2026-05-24", "2026-05-24 (Sunday)", "output/daily_html/digest_2026-05-24.html"),
                ("2026-05-22", "2026-05-22 (Friday)", "output/daily_html/digest_2026-05-22.html"),
            ],
        )

    def test_superpower_daily_is_a_default_blog_source(self):
        source = next(
            source
            for source in blog_reading_ranker.DEFAULT_SOURCES
            if source["name"] == "Superpower Daily"
        )

        self.assertEqual(
            source["url"],
            "https://www.superpowerdaily.com/archive?tags=%F0%9F%93%AC+Daily+Newsletter",
        )

    def test_github_pages_publish_files_include_all_archived_digest_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            daily_html_dir = Path(tmp)
            (daily_html_dir / "digest_2026-05-01.html").write_text("older", encoding="utf-8")
            (daily_html_dir / "digest_2026-05-24.html").write_text("current", encoding="utf-8")

            files = daily_digest.github_pages_publish_files(
                dt.date(2026, 5, 24),
                {"ranker_output_dir": "output/ranker_diagnostics"},
                daily_html_dir=daily_html_dir,
            )

        self.assertIn("output/daily_html/digest_2026-05-01.html", files)
        self.assertIn("output/daily_html/digest_2026-05-24.html", files)

    def test_mit_sloan_review_and_rachel_are_not_default_blog_sources(self):
        names = {source["name"] for source in blog_reading_ranker.DEFAULT_SOURCES}

        self.assertNotIn("MIT Sloan Review", names)
        self.assertNotIn("Rachel by the Bay", names)

    def test_hn_companion_overview_is_extracted_from_cached_summary(self):
        summary = (
            "# Overview\n"
            "Commenters focus on pricing, reliability, and possible use cases.\n\n"
            "# Main Themes & Key Insights\n"
            "* Pricing debate"
        )

        self.assertEqual(
            daily_digest.extract_hn_companion_overview(summary),
            "Commenters focus on pricing, reliability, and possible use cases.",
        )

    def test_hn_articles_render_hn_companion_link_and_overview(self):
        html = daily_digest.generate_html(
            dt.date(2026, 5, 24),
            {
                "hn": [{
                    "title": "Example HN story",
                    "url": "https://example.com/story",
                    "hn_url": "https://news.ycombinator.com/item?id=123",
                    "hn_companion_url": "https://app.hncompanion.com/item?id=123",
                    "discussion_overview": "Commenters compare trade-offs and implementation details.",
                    "score": 100,
                    "comments": 25,
                    "outlet": "HN",
                    "source": "HackerNews",
                    "date": dt.date(2026, 5, 24),
                }],
                "nyt_wsj": [],
                "blogs": [],
                "linkedin": [],
            },
        )

        self.assertIn('href="https://app.hncompanion.com/item?id=123"', html)
        self.assertIn("HN Companion", html)
        self.assertIn("Commenters compare trade-offs and implementation details.", html)

    def test_user_input_date_runs_digest_for_prior_day(self):
        self.assertEqual(
            daily_digest.content_date_from_user_input("2026-05-24"),
            dt.date(2026, 5, 23),
        )

    def test_invalid_user_input_date_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            daily_digest.content_date_from_user_input("05/24/2026")

    def test_summary_provider_defaults_to_gemini_and_can_be_disabled(self):
        self.assertEqual(daily_digest.parse_args([]).summary_provider, "gemini")
        self.assertEqual(
            daily_digest.parse_args(["--summary-provider", "none"]).summary_provider,
            "none",
        )

    def test_claude_summary_provider_requires_nyt_sections(self):
        with self.assertRaisesRegex(ValueError, "--summary-nyt-sections"):
            daily_digest.summary_config_from_args(
                daily_digest.parse_args(["--summary-provider", "claude-sonnet"])
            )

    def test_claude_summary_provider_uses_claude_for_blogs_and_selected_nyt_sections(self):
        config = daily_digest.summary_config_from_args(
            daily_digest.parse_args([
                "--summary-provider", "claude-sonnet",
                "--summary-nyt-sections", "Technology / AI,Opinion / Analysis",
            ])
        )

        self.assertEqual(
            daily_digest.article_summary_provider(
                {"topic_tag": "Technology / AI"}, "nyt_wsj", config
            ),
            "claude-sonnet",
        )
        self.assertEqual(
            daily_digest.article_summary_provider(
                {"topic_tag": "Business / Economy / Markets"}, "nyt_wsj", config
            ),
            "gemini",
        )
        self.assertEqual(
            daily_digest.article_summary_provider(
                {"source": "Simon Willison"}, "blogs", config
            ),
            "claude-sonnet",
        )

    def test_article_overview_renders_for_nyt_and_blog_items(self):
        html = daily_digest.generate_html(
            dt.date(2026, 5, 24),
            {
                "hn": [],
                "nyt_wsj": [{
                    "title": "Policy story",
                    "url": "https://example.com/policy",
                    "outlet": "NYT",
                    "source": "NYT",
                    "topic_tag": "Politics / U.S.",
                    "article_overview": "Concise overview of policy stakes and trade-offs.",
                }],
                "blogs": [{
                    "title": "Engineering post",
                    "url": "https://example.com/post",
                    "source": "Simon Willison",
                    "topic_tag": "Technology",
                    "article_overview": "Concise overview of engineering idea and why it matters.",
                }],
                "linkedin": [],
            },
        )
        md = daily_digest.generate_markdown(
            dt.date(2026, 5, 24),
            {
                "hn": [],
                "nyt_wsj": [{
                    "title": "Policy story",
                    "url": "https://example.com/policy",
                    "outlet": "NYT",
                    "source": "NYT",
                    "topic_tag": "Politics / U.S.",
                    "article_overview": "Concise overview of policy stakes and trade-offs.",
                }],
                "blogs": [{
                    "title": "Engineering post",
                    "url": "https://example.com/post",
                    "source": "Simon Willison",
                    "topic_tag": "Technology",
                    "article_overview": "Concise overview of engineering idea and why it matters.",
                }],
                "linkedin": [],
            },
        )

        self.assertIn("Concise overview of policy stakes and trade-offs.", html)
        self.assertIn("Concise overview of engineering idea and why it matters.", html)
        self.assertIn("Concise overview of policy stakes and trade-offs.", md)
        self.assertIn("Concise overview of engineering idea and why it matters.", md)

    def test_reading_time_uses_230_wpm_ceiling(self):
        self.assertEqual(daily_digest.reading_time_minutes(1), 1)
        self.assertEqual(daily_digest.reading_time_minutes(230), 1)
        self.assertEqual(daily_digest.reading_time_minutes(231), 2)

    def test_extract_word_count_from_article_html_ignores_scripts(self):
        words = " ".join(f"word{i}" for i in range(240))
        html = f"""
        <html><head><script>{"bad " * 100}</script></head>
        <body><article><h1>Title</h1><p>{words}</p></article></body></html>
        """

        self.assertEqual(daily_digest.article_word_count_from_html(html), 241)

    def test_word_count_and_reading_time_render_in_html_and_markdown(self):
        data = {
            "hn": [{
                "title": "Example HN story",
                "url": "https://example.com/story",
                "outlet": "HN",
                "source": "HackerNews",
                "score": 100,
                "comments": 25,
                "word_count": 690,
                "reading_time_minutes": 3,
            }],
            "nyt_wsj": [],
            "blogs": [],
            "linkedin": [],
        }

        html = daily_digest.generate_html(dt.date(2026, 5, 24), data)
        md = daily_digest.generate_markdown(dt.date(2026, 5, 24), data)

        self.assertIn("690 words · 3 min read", html)
        self.assertIn("690 words · 3 min read", md)

    def test_enrich_article_reading_stats_uses_cache(self):
        data = {
            "hn": [{
                "title": "Cached article",
                "url": "https://example.com/cached",
            }],
            "nyt_wsj": [],
            "blogs": [],
            "linkedin": [],
        }
        cache = {
            "https://example.com/cached|cached article": {
                "word_count": 460,
                "reading_time_minutes": 2,
            }
        }

        daily_digest.enrich_article_reading_stats(data, cache=cache)

        self.assertEqual(data["hn"][0]["word_count"], 460)
        self.assertEqual(data["hn"][0]["reading_time_minutes"], 2)
