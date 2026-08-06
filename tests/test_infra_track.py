"""The ai-infra corpus track in the blog ranker.

The comprehension layer tracks two corpora separately: the AI conversation
everyone is having, and the AI infrastructure the reader sells into. This covers
the ranker half - source tagging and the guaranteed link allocation.
"""

import unittest

from src.rankers import blog_reading_ranker as ranker


def _candidate(source, category, track, score):
    return ranker.BlogCandidate(
        title=f"{source} post",
        canonical_url=f"https://{source}.example/{score}",
        original_url=f"https://{source}.example/{score}",
        source=source,
        category=category,
        track=track,
        score=score,
    )


class InfraSourceTests(unittest.TestCase):
    def test_infra_sources_are_present_and_configured(self):
        infra = [s for s in ranker.DEFAULT_SOURCES if ranker.track_for_source(s) == ranker.AI_INFRA]

        self.assertGreaterEqual(len(infra), 6)
        for source in infra:
            self.assertEqual(source["category"], ranker.INFRA_CATEGORY)
            # Every infra feed was verified to return dated entries; a source
            # with no feed_url would silently contribute nothing.
            self.assertTrue(source["feed_url"], source["name"])
            self.assertTrue(source["url"])

    def test_existing_sources_stay_on_the_general_track(self):
        for source in ranker.DEFAULT_SOURCES:
            if source["category"] != ranker.INFRA_CATEGORY:
                self.assertEqual(ranker.track_for_source(source), ranker.AI_AT_LARGE, source["name"])

    def test_an_explicit_track_on_a_source_wins(self):
        self.assertEqual(
            ranker.track_for_source({"category": "Tech & Engineering", "track": ranker.AI_INFRA}),
            ranker.AI_INFRA,
        )
        self.assertEqual(ranker.track_for_source({}), ranker.AI_AT_LARGE)

    def test_the_infra_category_has_a_weight_and_a_diversity_cap(self):
        self.assertIn(ranker.INFRA_CATEGORY, ranker.CATEGORY_WEIGHTS)
        self.assertIn(ranker.INFRA_CATEGORY, ranker.DIVERSITY_CAPS)


class TrackPassthroughTests(unittest.TestCase):
    def test_articles_carry_their_track_and_infra_topic(self):
        article = ranker.candidate_to_article(
            _candidate("DataCenter Dynamics", ranker.INFRA_CATEGORY, ranker.AI_INFRA, 80.0)
        )

        self.assertEqual(article["track"], ranker.AI_INFRA)
        self.assertEqual(article["topic"], "Infrastructure")
        self.assertEqual(article["topic_tag"], "Infrastructure")
        self.assertEqual(article["category"], "tech")

    def test_general_articles_are_unchanged(self):
        article = ranker.candidate_to_article(
            _candidate("Simon Willison", "Tech & Engineering", ranker.AI_AT_LARGE, 70.0)
        )

        self.assertEqual(article["track"], ranker.AI_AT_LARGE)
        self.assertEqual(article["topic"], "Technology")

    def test_the_diagnostic_csv_records_the_track(self):
        import csv
        import tempfile
        from pathlib import Path

        candidates = [
            _candidate("DataCenter Dynamics", ranker.INFRA_CATEGORY, ranker.AI_INFRA, 80.0),
            _candidate("Simon Willison", "Tech & Engineering", ranker.AI_AT_LARGE, 70.0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            import datetime as dt

            ranker.write_outputs(candidates, candidates, [], dt.date(2026, 8, 4), Path(tmp))
            with (Path(tmp) / "blog_candidates_2026-08-04.csv").open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual({row["track"] for row in rows}, {ranker.AI_INFRA, ranker.AI_AT_LARGE})


class AllocationTests(unittest.TestCase):
    def test_infra_items_are_selected_even_when_outscored_by_everything_else(self):
        """The point of a separate allocation: no crowding out on a busy day."""
        general = [
            _candidate(f"General {n}", "Tech & Engineering", ranker.AI_AT_LARGE, 90.0 + n)
            for n in range(10)
        ]
        infra = [
            _candidate(f"Infra {n}", ranker.INFRA_CATEGORY, ranker.AI_INFRA, 10.0 + n)
            for n in range(4)
        ]

        selected = ranker.select_by_track(general + infra, max_links=5, infra_max_links=3)

        tracks = [item.track for item in selected]
        self.assertEqual(tracks.count(ranker.AI_INFRA), 3)
        self.assertEqual(tracks.count(ranker.AI_AT_LARGE), 5)
        # Merged by score, so the strongest general item still leads the list.
        self.assertEqual(selected[0].score, 99.0)

    def test_a_zero_infra_allocation_selects_none(self):
        infra = [_candidate("Infra", ranker.INFRA_CATEGORY, ranker.AI_INFRA, 50.0)]

        selected = ranker.select_by_track(infra, max_links=5, infra_max_links=0)

        self.assertEqual(selected, [])

    def test_selection_without_infra_candidates_is_unchanged(self):
        general = [
            _candidate(f"General {n}", "Tech & Engineering", ranker.AI_AT_LARGE, 50.0 + n)
            for n in range(3)
        ]

        selected = ranker.select_by_track(general, max_links=2, infra_max_links=6)

        self.assertEqual(len(selected), 2)
        self.assertEqual([item.score for item in selected], [52.0, 51.0])


if __name__ == "__main__":
    unittest.main()
