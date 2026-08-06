import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from src import daily_digest
from src.comprehension import (
    deepread, llm, narrative, render, republish, run, sources, store, triage, weekly,
)


class SourcesTests(unittest.TestCase):
    def test_item_key_matches_daily_digest_article_key(self):
        """Pinned invariant: the JSON and Markdown readers must agree on identity."""
        for url, title in (
            ("https://example.com/story/", "Example Story"),
            ("https://example.com/story", "Example Story"),
            ("", "Only A Title"),
        ):
            self.assertEqual(
                sources.item_key(url, title),
                daily_digest.article_key({"url": url, "title": title}),
            )

    def test_md_unescape_reverses_md_escape(self):
        for raw in ("Plain title", "Brackets [here]", "Pipe | pipe", "Back\\slash"):
            self.assertEqual(sources.md_unescape(daily_digest._md_escape(raw)), raw)

    def test_markdown_parses_current_heading_names(self):
        text = "\n".join([
            "# Daily Digest - Monday, August 3, 2026",
            "",
            "### 🔶 Yesterday's Top HackerNews",
            "",
            "1. **[HN]** [An HN story](https://example.com/a) 570 pts · 389 comments · 2026-08-02",
            "   - **Overview:** Commenters debate the trade-offs.",
            "",
            "### 📰 Today on The New York Times",
            "",
            "#### AI",
            "",
            "1. **[NYT · AI]** [A news story](https://nytimes.com/b) · score 86.9 · Published: Mon, 2026-08-03",
            "   - **Abstract:** The abstract.",
            "   - **Overview (Model: Gemini 3.5 Flash):** The overview.",
            "   - Read deeply — matches technology / ai",
            "",
            "### 👽 Yesterday on Reddit",
            "",
            "#### r/ClaudeAI",
            "",
            "1. **[Reddit · r/ClaudeAI]** [A post](https://reddit.com/c) · 2026-08-02",
            "   - **Bot TL;DR:** The bot summary.",
        ])

        items = sources.parse_markdown(text, "2026-08-03")

        self.assertEqual([item["group"] for item in items], ["hn", "nyt_wsj", "reddit"])
        hn, nyt, reddit = items
        self.assertEqual(hn["title"], "An HN story")
        self.assertEqual(hn["score"], 570.0)
        self.assertEqual(hn["date"], "2026-08-02")
        self.assertEqual(hn["prose"], "Commenters debate the trade-offs.")
        self.assertEqual(nyt["score"], 86.9)
        self.assertEqual(nyt["section"], "AI")
        # The richer LLM overview wins over the RSS abstract.
        self.assertEqual(nyt["prose"], "The overview.")
        self.assertEqual(reddit["prose"], "The bot summary.")
        self.assertEqual(reddit["section"], "r/ClaudeAI")

    def test_markdown_parses_historical_heading_names(self):
        """Early digests used different section headings; all variants must map."""
        text = "\n".join([
            "### 🔶 Hacker News Top 16",
            "",
            "1. **[HN]** [Older story](https://example.com/x) 100 pts · 5 comments · 2026-04-21",
            "",
            "### 📰 NYT / WSJ Strategic Reading List",
            "",
            "1. **[NYT]** [Older news](https://nytimes.com/y) · score 50.0 · 2026-04-21",
            "",
            "### 📚 Blogs & Craft",
            "",
            "1. **[Simon Willison]** [Older post](https://simonwillison.net/z) · score 40.0 · 2026-04-21",
        ])

        items = sources.parse_markdown(text, "2026-04-22")

        self.assertEqual([item["group"] for item in items], ["hn", "nyt_wsj", "blogs"])

    def test_markdown_skips_linkedin_and_untitled_rows(self):
        text = "\n".join([
            "### 💼 LinkedIn - Rama's Activity",
            "",
            "- **[LinkedIn]** [Some activity](https://linkedin.com/a)",
            "",
            "### 📚 Blog Posts",
            "",
            "1. **[Blog]** [](#)",
        ])

        self.assertEqual(sources.parse_markdown(text, "2026-08-03"), [])

    def test_items_from_digest_json_normalizes_and_skips_linkedin(self):
        payload = {
            "digest_date": "2026-08-04",
            "sections": {
                "nyt_wsj": [{
                    "title": "A story",
                    "url": "https://nytimes.com/a",
                    "source": "NYT Business",
                    "topic_tag": "Business",
                    "group": "nyt_wsj",
                    "score": 80.0,
                    "abstract": "The abstract.",
                    "article_overview": "The overview.",
                    "item_key": "https://nytimes.com/a",
                }],
                "linkedin": [{"title": "Ignored", "url": "https://linkedin.com/x"}],
            },
        }

        items = sources.items_from_digest_json(payload)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["prose"], "The overview.")
        self.assertEqual(items[0]["section"], "Business")
        self.assertEqual(items[0]["digest_date"], "2026-08-04")
        self.assertEqual(items[0]["track"], "ai-at-large")

    def test_track_map_assigns_the_infra_track(self):
        item = sources.normalize_item(
            {"title": "T", "url": "https://dcd.example/x", "source": "DataCenter Dynamics"},
            track_map={"DataCenter Dynamics": "ai-infra"},
        )

        self.assertEqual(item["track"], "ai-infra")


class ThreadRegistryTests(unittest.TestCase):
    def test_slugs_are_derived_and_deduplicated(self):
        self.assertEqual(store.slugify("800V DC in the rack!"), "800v-dc-in-the-rack")
        self.assertEqual(store.unique_slug("Same Name", {"same-name"}), "same-name-2")

    def test_long_slugs_truncate_at_a_word_boundary(self):
        slug = store.slugify("Data-center buildout hits local and regulatory pushback")

        self.assertEqual(slug, "data-center-buildout-hits-local-and")
        self.assertFalse(slug.endswith("-"))

    def test_record_item_dedupes_by_item_key(self):
        registry = store.new_registry()
        thread = store.create_thread(registry, "A thread", as_of="2026-08-01")
        item = {"item_key": "https://example.com/a", "title": "A", "url": "https://example.com/a"}

        self.assertTrue(store.record_item(registry, thread["slug"], item, as_of="2026-08-01"))
        self.assertFalse(store.record_item(registry, thread["slug"], item, as_of="2026-08-02"))
        self.assertEqual(len(thread["items"]), 1)
        self.assertEqual(thread["last_update"], "2026-08-01")

    def test_merge_redirects_the_loser_slug_and_keeps_both_histories(self):
        registry = store.new_registry()
        winner = store.create_thread(registry, "Winner", as_of="2026-07-01")
        loser = store.create_thread(registry, "Loser", as_of="2026-07-10")
        store.record_item(registry, winner["slug"], {"item_key": "w1", "title": "W1"}, as_of="2026-07-01")
        store.record_item(registry, loser["slug"], {"item_key": "l1", "title": "L1"}, as_of="2026-07-10")

        self.assertTrue(store.merge_thread(registry, loser["slug"], winner["slug"], as_of="2026-07-11"))

        self.assertEqual(loser["status"], "merged")
        self.assertEqual(loser["merged_into"], winner["slug"])
        self.assertEqual({entry["item_key"] for entry in winner["items"]}, {"w1", "l1"})
        # The loser slug still resolves — nothing is destroyed, so a later
        # assignment to the old slug still lands in the right place.
        self.assertEqual(store.resolve_slug(registry, loser["slug"]), winner["slug"])
        self.assertTrue(
            store.record_item(registry, loser["slug"], {"item_key": "l2", "title": "L2"}, as_of="2026-07-12")
        )
        self.assertEqual(len(winner["items"]), 3)

    def test_a_slug_reconstructed_from_the_display_name_still_resolves(self):
        """Regression: the real backfill created three threads for one story.

        Slugs are truncated to max_length but display names are not, so a caller
        that rebuilds the slug from the name overshoots. These are the exact
        reference/slug pairs observed in the 6-week backfill.
        """
        registry = store.new_registry()
        for name in (
            "Newer flagship models show worse tool-use reliability",
            "Claude Sonnet 5 launch gets mixed reception",
            "GPT-5.6 launch reshapes competitive landscape",
            "Global tech sell-off on AI valuation jitters",
            "Data-center buildout meets grid and community friction",
        ):
            thread = store.create_thread(registry, name, as_of="2026-07-01")
            overshot = store.slugify(name, max_length=200)

            self.assertNotEqual(overshot, thread["slug"], name)
            self.assertEqual(store.resolve_slug(registry, overshot), thread["slug"], name)

    def test_normalization_does_not_invent_a_match(self):
        registry = store.new_registry()
        store.create_thread(registry, "A real thread", as_of="2026-07-01")

        self.assertIsNone(store.resolve_slug(registry, "Something Else Entirely"))
        self.assertIsNone(store.resolve_slug(registry, ""))

    def test_resolve_slug_is_safe_on_unknown_and_cyclic_input(self):
        registry = store.new_registry()
        a = store.create_thread(registry, "A")
        b = store.create_thread(registry, "B")
        a["merged_into"], b["merged_into"] = b["slug"], a["slug"]

        self.assertIsNone(store.resolve_slug(registry, "nope"))
        self.assertIn(store.resolve_slug(registry, a["slug"]), {a["slug"], b["slug"]})

    def test_dormancy_transitions_at_the_boundary(self):
        registry = store.new_registry()
        thread = store.create_thread(registry, "Quiet", as_of="2026-01-01")
        store.record_item(registry, thread["slug"], {"item_key": "k", "title": "T"}, as_of="2026-01-01")

        self.assertEqual(store.retire_dormant(registry, "2026-01-22", idle_days=21), [])
        self.assertEqual(thread["status"], "active")
        self.assertEqual(store.retire_dormant(registry, "2026-01-23", idle_days=21), [thread["slug"]])
        self.assertEqual(thread["status"], "dormant")

        # A new item revives it.
        store.record_item(registry, thread["slug"], {"item_key": "k2", "title": "T2"}, as_of="2026-01-24")
        self.assertEqual(thread["status"], "active")

    def test_registry_round_trips_through_disk(self):
        registry = store.new_registry()
        store.create_thread(registry, "A thread", charter="Why it matters", track="ai-infra", as_of="2026-08-01")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "threads.json"
            store.save_threads(registry, path, as_of="2026-08-01")
            reloaded = store.load_threads(path)

        self.assertEqual(reloaded["updated"], "2026-08-01")
        self.assertEqual(reloaded["threads"][0]["track"], "ai-infra")

    def test_missing_registry_file_yields_an_empty_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = store.load_threads(Path(tmp) / "absent.json")

        self.assertEqual(registry["threads"], [])


class MarksTests(unittest.TestCase):
    def test_merge_is_order_independent_and_idempotent(self):
        early = {"items": {"k": {"mark": "knew-this", "recorded_at": "2026-08-01T10:00:00Z"}}}
        late = {"items": {"k": {"mark": "useful", "recorded_at": "2026-08-02T10:00:00Z"}}}

        forward, backward = store.new_marks(), store.new_marks()
        store.merge_marks(forward, early)
        store.merge_marks(forward, late)
        store.merge_marks(backward, late)
        store.merge_marks(backward, early)

        self.assertEqual(forward["items"], backward["items"])
        self.assertEqual(forward["items"]["k"]["mark"], "useful")

    def test_empty_and_invalid_marks_are_a_no_op(self):
        marks = store.new_marks()

        self.assertEqual(store.merge_marks(marks, {}), 0)
        self.assertEqual(store.merge_marks(marks, {"items": {"k": {"mark": "nonsense"}}}), 0)
        self.assertEqual(marks["items"], {})

    def test_harvest_absorbs_then_archives_downloaded_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads, archive = Path(tmp) / "dl", Path(tmp) / "archive"
            downloads.mkdir()
            marks_path = Path(tmp) / "marks.json"
            (downloads / "dd-marks-2026-08-04.json").write_text(
                json.dumps({"items": {"https://example.com/a": {
                    "mark": "over-my-head", "recorded_at": "2026-08-04T09:00:00Z"}}}),
                encoding="utf-8",
            )

            result = store.harvest_marks(downloads, marks_path, archive)
            marks = store.load_marks(marks_path)

            self.assertEqual(result, {"files": 1, "marks": 1})
            self.assertEqual(marks["items"]["https://example.com/a"]["mark"], "over-my-head")
            self.assertEqual([p.name for p in archive.iterdir()], ["dd-marks-2026-08-04.json"])
            self.assertEqual(list(downloads.glob("dd-marks-*.json")), [])
            # Silence must be safe: a second harvest changes nothing.
            self.assertEqual(store.harvest_marks(downloads, marks_path, archive), {"files": 0, "marks": 0})

    def test_harvest_is_a_no_op_when_the_downloads_dir_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = store.harvest_marks(Path(tmp) / "absent", Path(tmp) / "marks.json", Path(tmp) / "a")

        self.assertEqual(result, {"files": 0, "marks": 0})

    def test_altitude_note_names_marked_items_via_the_registry(self):
        registry, moving, _ = _registry_with_movement()
        marks = {"items": {
            "k1": {"mark": "knew-this"},
            "k2": {"mark": "over-my-head"},
            "k3": {"mark": "useful"},
            "unknown-key": {"mark": "knew-this"},
        }}

        note = store.marks_altitude_note(marks, registry)

        self.assertIn("already knew", note)
        self.assertIn("First move", note)
        self.assertIn("over their head", note)
        self.assertIn("Second move", note)
        # "useful" is neither a skip nor a struggle signal, and unresolvable keys drop.
        self.assertNotIn("Old news", note)
        self.assertNotIn("unknown-key", note)

    def test_thread_marks_are_namespaced_and_resolve_to_the_thread_name(self):
        registry, moving, _ = _registry_with_movement()
        key = store.thread_mark_key(moving["slug"])

        self.assertEqual(key, f"thread:{moving['slug']}")
        self.assertEqual(
            store.labels_by_mark_key(registry)[key], f'the thread "{moving["name"]}"'
        )
        # Namespacing keeps thread keys from ever colliding with an item URL.
        self.assertNotIn(key, {e["item_key"] for t in registry["threads"] for e in t["items"]})

    def test_altitude_note_leads_with_thread_feedback(self):
        """A thread mark judges an explanation; an item mark only judges a headline."""
        registry, moving, _ = _registry_with_movement()
        marks = {"items": {
            "k1": {"mark": "over-my-head"},
            store.thread_mark_key(moving["slug"]): {"mark": "over-my-head"},
        }}

        note = store.marks_altitude_note(marks, registry)

        self.assertIn(f'the thread "{moving["name"]}"', note)
        self.assertIn("First move", note)
        self.assertLess(note.index("the thread"), note.index("First move"))

    def test_a_thread_mark_alone_still_produces_a_note(self):
        registry, moving, _ = _registry_with_movement()
        marks = {"items": {store.thread_mark_key(moving["slug"]): {"mark": "knew-this"}}}

        note = store.marks_altitude_note(marks, registry)

        self.assertIn("do not", note)
        self.assertIn(moving["name"], note)

    def test_altitude_note_is_empty_when_there_is_nothing_to_say(self):
        registry, _, _ = _registry_with_movement()

        self.assertEqual(store.marks_altitude_note(store.new_marks(), registry), "")
        self.assertEqual(
            store.marks_altitude_note({"items": {"k3": {"mark": "useful"}}}, registry), ""
        )

    def test_marks_hint_splits_known_from_hard(self):
        marks = {"items": {
            "a": {"mark": "knew-this"},
            "b": {"mark": "over-my-head"},
            "c": {"mark": "useful"},
        }}

        hint = store.marks_hint(marks)

        self.assertEqual(hint["knew_this"], ["a"])
        self.assertEqual(hint["over_my_head"], ["b"])


def _items(count=3):
    return [
        {
            "item_key": f"https://example.com/{index}",
            "title": f"Item {index}",
            "url": f"https://example.com/{index}",
            "source": "HackerNews",
            "group": "hn",
            "prose": f"Prose for item {index}.",
        }
        for index in range(1, count + 1)
    ]


class TriageTests(unittest.TestCase):
    def test_prompt_shows_existing_threads_and_todays_items(self):
        registry = store.new_registry()
        store.create_thread(registry, "800V DC in the rack", charter="Rack-level DC power.",
                            track="ai-infra", as_of="2026-07-01")

        prompt = triage.build_prompt(_items(2), registry, "2026-08-04", profile_text="Reader notes here.")

        self.assertIn("Reader notes here.", prompt)
        self.assertIn("800v-dc-in-the-rack", prompt)
        self.assertIn("Rack-level DC power.", prompt)
        self.assertIn("[1] Item 1", prompt)
        self.assertIn("Prose for item 2.", prompt)

    def test_apply_files_items_and_opens_declared_threads(self):
        registry = store.new_registry()
        response = {
            "new_threads": [{"key": "t1", "name": "A New Story", "charter": "What it is.", "track": "ai-infra"}],
            "assignments": [
                {"item": 1, "thread": "t1", "note": "Opens the story.", "promote": True},
                {"item": 2, "thread": "t1", "note": "Second data point.", "promote": False},
            ],
            "merges": [],
        }

        counts = triage.apply_triage(response, _items(3), registry, "2026-08-04", verbose=False)

        self.assertEqual(counts["created"], 1)
        self.assertEqual(counts["filed"], 2)
        self.assertEqual(counts["promoted"], ["https://example.com/1"])
        thread = registry["threads"][0]
        self.assertEqual(thread["track"], "ai-infra")
        self.assertEqual(len(thread["items"]), 2)
        self.assertEqual(thread["items"][0]["note"], "Opens the story.")
        # Item 3 was deliberately left unassigned and must not be filed anywhere.
        self.assertNotIn("https://example.com/3", [entry["item_key"] for entry in thread["items"]])

    def test_apply_is_idempotent_on_a_repeated_response(self):
        registry = store.new_registry()
        response = {
            "new_threads": [{"key": "t1", "name": "A Story", "charter": "", "track": "ai-at-large"}],
            "assignments": [{"item": 1, "thread": "t1", "note": "n", "promote": False}],
            "merges": [],
        }
        items = _items(2)

        triage.apply_triage(response, items, registry, "2026-08-04", verbose=False)
        second = triage.apply_triage(response, items, registry, "2026-08-05", verbose=False)

        # The second pass opens a distinct thread (a fresh key), but the item is
        # already filed under the first, so it is not double-counted there.
        self.assertEqual(second["filed"], 1)
        self.assertEqual(sum(len(t["items"]) for t in registry["threads"]), 2)

    def test_apply_reports_the_new_thread_cap_rather_than_truncating_silently(self):
        registry = store.new_registry()
        response = {
            "new_threads": [
                {"key": f"t{n}", "name": f"Thread {n}", "charter": "", "track": "ai-at-large"}
                for n in range(1, 6)
            ],
            "assignments": [{"item": 1, "thread": "t5", "note": "n", "promote": False}],
            "merges": [],
        }

        counts = triage.apply_triage(response, _items(1), registry, "2026-08-04",
                                     max_new_threads=2, verbose=False)

        self.assertEqual(counts["created"], 2)
        self.assertEqual(counts["dropped_new_threads"], 3)
        # t5 was dropped by the cap, so its assignment is reported, not filed.
        self.assertEqual(counts["filed"], 0)
        self.assertEqual(counts["unknown_threads"], ["t5"])

    def test_undeclared_thread_references_are_recovered_not_dropped(self):
        """The model sometimes invents a slug instead of declaring the thread."""
        registry = store.new_registry()
        response = {
            "new_threads": [],
            "assignments": [
                {"item": 1, "thread": "ai-driven-memory-shortage", "note": "n1", "promote": False},
                {"item": 2, "thread": "ai-driven-memory-shortage", "note": "n2", "promote": False},
            ],
            "merges": [],
        }

        counts = triage.apply_triage(response, _items(2), registry, "2026-08-04", verbose=False)

        self.assertEqual(counts["recovered_threads"], 1)
        self.assertEqual(counts["created"], 1)
        self.assertEqual(counts["filed"], 2)
        self.assertEqual(counts["unknown_threads"], [])
        thread = registry["threads"][0]
        self.assertEqual(thread["slug"], "ai-driven-memory-shortage")
        self.assertEqual(thread["name"], "Ai driven memory shortage")
        self.assertIn("need review", thread["charter"])

    def test_recovery_still_respects_the_new_thread_cap(self):
        registry = store.new_registry()
        response = {
            "new_threads": [{"key": "t1", "name": "Declared", "charter": "", "track": "ai-at-large"}],
            "assignments": [
                {"item": 1, "thread": "t1", "note": "", "promote": False},
                {"item": 2, "thread": "invented-a", "note": "", "promote": False},
                {"item": 3, "thread": "invented-b", "note": "", "promote": False},
            ],
            "merges": [],
        }

        counts = triage.apply_triage(response, _items(3), registry, "2026-08-04",
                                     max_new_threads=2, verbose=False)

        self.assertEqual(counts["created"], 2)
        self.assertEqual(counts["recovered_threads"], 1)
        self.assertEqual(counts["filed"], 2)
        self.assertEqual(counts["unknown_threads"], ["invented-b"])

    def test_apply_ignores_out_of_range_items_and_bad_merges(self):
        registry = store.new_registry()
        existing = store.create_thread(registry, "Known", as_of="2026-08-01")
        response = {
            "new_threads": [],
            "assignments": [
                {"item": 99, "thread": existing["slug"], "note": "", "promote": False},
                {"item": 0, "thread": existing["slug"], "note": "", "promote": False},
                {"item": 1, "thread": "", "note": "", "promote": False},
                {"item": 2, "thread": existing["slug"], "note": "", "promote": False},
            ],
            # No cap left for recovery, so the unresolvable merge is simply dropped.
            "merges": [{"loser": "nope", "winner": existing["slug"], "why": ""}],
        }

        counts = triage.apply_triage(response, _items(2), registry, "2026-08-04",
                                     max_new_threads=0, verbose=False)

        self.assertEqual(counts["filed"], 1)
        self.assertEqual(counts["merged"], 0)
        self.assertEqual(counts["unknown_threads"], [])

    def test_triage_day_reports_failure_when_the_model_is_unavailable(self):
        registry = store.new_registry()
        client = llm.ClaudeClient(cache={}, cache_path=None, verbose=False)
        client._client_error = "no api key"

        result = triage.triage_day(_items(2), registry, client, "2026-08-04", verbose=False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["filed"], 0)
        self.assertEqual(registry["threads"], [])

    def test_triage_day_on_no_items_is_a_no_op(self):
        result = triage.triage_day([], store.new_registry(), None, "2026-08-04", verbose=False)

        self.assertEqual(result["items"], 0)
        self.assertFalse(result["ok"])


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text="", stop_reason="end_turn"):
        self.content = [_FakeBlock(text)] if text else []
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


class ClaudeClientTests(unittest.TestCase):
    def _client(self, responses, **kwargs):
        fake = _FakeClient(responses)
        client = llm.ClaudeClient(cache={}, cache_path=None, verbose=False, client=fake, **kwargs)
        return client, fake

    def test_request_omits_sampling_params_and_sets_effort(self):
        """temperature/top_p/top_k are rejected with a 400 on these models."""
        client, fake = self._client([_FakeResponse("hello")], effort="low")

        self.assertEqual(client.complete("prompt", system="sys"), "hello")
        request = fake.messages.requests[0]
        self.assertEqual(request["output_config"], {"effort": "low"})
        self.assertEqual(request["system"], "sys")
        self.assertNotIn("temperature", request)
        self.assertNotIn("top_p", request)
        self.assertNotIn("top_k", request)
        self.assertNotIn("thinking", request)

    def test_schema_requests_json_and_returns_a_parsed_object(self):
        client, fake = self._client([_FakeResponse('{"a": 1}')])
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["a"], "properties": {"a": {"type": "integer"}}}

        self.assertEqual(client.complete("prompt", schema=schema), {"a": 1})
        self.assertEqual(
            fake.messages.requests[0]["output_config"]["format"],
            {"type": "json_schema", "schema": schema},
        )

    def test_identical_calls_hit_the_cache(self):
        client, fake = self._client([_FakeResponse("once")])

        self.assertEqual(client.complete("prompt"), "once")
        self.assertEqual(client.complete("prompt"), "once")
        self.assertEqual(len(fake.messages.requests), 1)
        self.assertEqual(client.cache_hits, 1)

    def test_cache_key_separates_prompt_effort_and_schema(self):
        client, fake = self._client([_FakeResponse("a"), _FakeResponse("b"), _FakeResponse("c")])

        client.complete("prompt")
        client.complete("prompt", effort="high")
        client.complete("other")

        self.assertEqual(len(fake.messages.requests), 3)

    def test_refusal_and_empty_content_return_none(self):
        client, _ = self._client([_FakeResponse("", stop_reason="refusal"), _FakeResponse("")])

        self.assertIsNone(client.complete("a"))
        self.assertIsNone(client.complete("b"))

    def test_unparseable_json_is_a_failure_not_a_crash(self):
        client, _ = self._client([_FakeResponse("not json")])

        self.assertIsNone(client.complete("prompt", schema={"type": "object"}))

    def test_breaker_trips_after_repeated_failures(self):
        errors = [RuntimeError("boom")] * 3
        client, fake = self._client(errors + [_FakeResponse("late")], failure_limit=3)

        for _ in range(3):
            self.assertIsNone(client.complete("prompt", effort="low") or None)
        self.assertTrue(client.tripped)
        self.assertFalse(client.available)
        self.assertIsNone(client.complete("another prompt"))
        # The fourth response was never requested.
        self.assertEqual(len(fake.messages.requests), 3)

    def test_cache_persists_to_disk_only_when_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            fake = _FakeClient([_FakeResponse("value")])
            client = llm.ClaudeClient(cache={}, cache_path=path, verbose=False, client=fake)

            client.complete("prompt")
            self.assertFalse(path.exists())
            client.save_cache()

            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))), 1)


def _registry_with_movement():
    """One thread with prior history that moved today, one that stayed quiet."""
    registry = store.new_registry()
    moving = store.create_thread(
        registry, "800V DC in the rack", charter="Rack-level DC power distribution.",
        track="ai-infra", as_of="2026-07-01",
    )
    store.record_item(registry, moving["slug"],
                      {"item_key": "k1", "title": "First move", "url": "https://example.com/1",
                       "source": "DCD"}, as_of="2026-07-01", note="Opened the story.")
    store.record_item(registry, moving["slug"],
                      {"item_key": "k2", "title": "Second move", "url": "https://example.com/2",
                       "source": "OCP"}, as_of="2026-08-04", note="A vendor commits.")
    quiet = store.create_thread(registry, "Agentic coding tools", track="ai-at-large", as_of="2026-07-20")
    store.record_item(registry, quiet["slug"],
                      {"item_key": "k3", "title": "Old news", "url": "https://example.com/3",
                       "source": "HN"}, as_of="2026-07-20")
    return registry, moving, quiet


_NARRATIVE = {
    "headline": "A second vendor commits to 800V racks",
    "what_changed": "The story moved from proposal to commitment.",
    "why_it_matters": "Rack-level DC needs two vendors before it is a standard.",
}


class NarrativeTests(unittest.TestCase):
    def test_movement_is_split_from_quiet(self):
        registry, moving, quiet = _registry_with_movement()

        moved = narrative.threads_that_moved(registry, "2026-08-04")
        still = narrative.quiet_threads(registry, "2026-08-04")

        self.assertEqual([thread["slug"] for thread, _ in moved], [moving["slug"]])
        self.assertEqual([entry["item_key"] for _, today in moved for entry in today], ["k2"])
        self.assertEqual([thread["slug"] for thread in still], [quiet["slug"]])

    def test_quiet_list_excludes_threads_that_did_not_exist_yet(self):
        """Re-rendering an earlier date must not list threads discovered later."""
        registry, moving, quiet = _registry_with_movement()
        future = store.create_thread(registry, "Discovered later", as_of="2026-08-04")
        store.record_item(registry, future["slug"], {"item_key": "kf", "title": "F"}, as_of="2026-08-04")

        on_the_day = [t["slug"] for t in narrative.quiet_threads(registry, "2026-07-20")]
        later = [t["slug"] for t in narrative.quiet_threads(registry, "2026-08-05")]

        self.assertNotIn(future["slug"], on_the_day)
        self.assertIn(future["slug"], later)
        self.assertIn(quiet["slug"], later)

    def test_prompt_carries_prior_history_and_todays_prose(self):
        registry, _, _ = _registry_with_movement()
        moved = narrative.threads_that_moved(registry, "2026-08-04")

        prompt = narrative.build_prompt(
            moved, "2026-08-04", items_by_key={"k2": {"prose": "Full article context."}}
        )

        self.assertIn("Where the story stood", prompt)
        self.assertIn("First move", prompt)
        self.assertIn("Opened the story.", prompt)
        self.assertIn("Full article context.", prompt)
        self.assertNotIn("no earlier history", prompt)

    def test_prompt_flags_a_brand_new_thread(self):
        registry = store.new_registry()
        thread = store.create_thread(registry, "Brand new", as_of="2026-08-04")
        store.record_item(registry, thread["slug"], {"item_key": "k", "title": "T"}, as_of="2026-08-04")

        prompt = narrative.build_prompt(
            narrative.threads_that_moved(registry, "2026-08-04"), "2026-08-04"
        )

        self.assertIn("no earlier history", prompt)

    def test_narrate_day_keeps_known_slugs_and_drops_the_rest(self):
        registry, moving, _ = _registry_with_movement()
        payload = {"threads": [
            dict(_NARRATIVE, slug=moving["slug"]),
            dict(_NARRATIVE, slug="hallucinated-slug"),
        ]}
        client = llm.ClaudeClient(
            cache={}, cache_path=None, verbose=False, client=_FakeClient([_FakeResponse(json.dumps(payload))])
        )

        narratives = narrative.narrate_day(registry, "2026-08-04", client)

        self.assertEqual(list(narratives), [moving["slug"]])
        self.assertEqual(narratives[moving["slug"]]["headline"], _NARRATIVE["headline"])

    def test_narrate_day_with_no_movement_makes_no_call(self):
        registry, _, _ = _registry_with_movement()
        fake = _FakeClient([])
        client = llm.ClaudeClient(cache={}, cache_path=None, verbose=False, client=fake)

        self.assertEqual(narrative.narrate_day(registry, "2026-08-05", client), {})
        self.assertEqual(fake.messages.requests, [])

    def test_narratives_round_trip_per_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "narratives.json"
            data = narrative.load_narratives(path)
            narrative.record_narratives(data, "2026-08-04", {"slug-a": _NARRATIVE})
            narrative.save_narratives(data, path)

            reloaded = narrative.load_narratives(path)

        self.assertEqual(narrative.narratives_for(reloaded, "2026-08-04")["slug-a"], _NARRATIVE)
        self.assertEqual(narrative.narratives_for(reloaded, "2026-01-01"), {})


class RenderTests(unittest.TestCase):
    def test_markdown_shows_movement_narrative_and_quiet_threads(self):
        registry, moving, quiet = _registry_with_movement()

        md = render.render_markdown(registry=registry, digest_date="2026-08-04",
                                   narratives={moving["slug"]: _NARRATIVE})

        self.assertIn("# AI Comprehension — Tuesday, August 4, 2026", md)
        self.assertIn("### AI infrastructure", md)
        self.assertIn("#### 800V DC in the rack", md)
        self.assertIn("2 items · 1 new today · tracked since 2026-07-01", md)
        # "new" must not be pluralized — a busy day read "5 news today".
        self.assertNotIn("news today", md)
        self.assertIn(_NARRATIVE["headline"], md)
        self.assertIn(f"**Why it matters:** {_NARRATIVE['why_it_matters']}", md)
        self.assertIn("[Second move](https://example.com/2)", md)
        self.assertIn("### Quiet threads", md)
        self.assertIn(quiet["name"], md)
        # The quiet thread's own items must not be rendered as today's movement.
        self.assertNotIn("Old news", md)

    def test_html_reuses_the_digest_chrome_and_links_back_to_the_day(self):
        registry, moving, _ = _registry_with_movement()

        html = render.render_html("2026-08-04", registry, {moving["slug"]: _NARRATIVE})

        self.assertIn("<title>AI Comprehension — Tuesday, August 4, 2026</title>", html)
        self.assertIn('href="../daily_html/digest_2026-08-04.html"', html)
        self.assertIn('href="comprehension_archive.html"', html)
        self.assertIn('data-tg="theme"', html)
        self.assertIn("AI infrastructure", html)
        self.assertIn(_NARRATIVE["what_changed"], html)
        self.assertIn("Why it matters", html)
        self.assertIn("1 threads moved · 1 quiet", html)

    def test_every_thread_offers_marks_and_the_page_can_export_them(self):
        """The companion page is the primary feedback surface: ~10 threads, not ~67 items."""
        registry, moving, _ = _registry_with_movement()

        html = render.render_html("2026-08-04", registry, {moving["slug"]: _NARRATIVE})

        self.assertIn(f'data-mark-key="thread:{moving["slug"]}"', html)
        self.assertEqual(html.count('class="marks"'), 1)  # one moved thread
        for value in ("knew-this", "useful", "over-my-head"):
            self.assertIn(f'data-mark="{value}"', html)
        self.assertIn('aria-label="How this explanation landed"', html)
        # Needed by the shared script for the export filename and the download.
        self.assertIn('data-digest-date="2026-08-04"', html)
        self.assertIn('id="saveMarks"', html)

    def test_quiet_threads_are_not_markable(self):
        """Nothing was explained, so there is no explanation to judge."""
        registry, _, quiet = _registry_with_movement()

        html = render.render_html("2026-08-04", registry, {})

        self.assertNotIn(f'data-mark-key="thread:{quiet["slug"]}"', html)

    def test_html_escapes_thread_and_narrative_text(self):
        registry = store.new_registry()
        thread = store.create_thread(registry, 'Thread <b>"one"</b>', as_of="2026-08-04")
        store.record_item(registry, thread["slug"],
                          {"item_key": "k", "title": "Item & <script>", "url": "https://e/x"},
                          as_of="2026-08-04")

        html = render.render_html("2026-08-04", registry,
                                  {thread["slug"]: dict(_NARRATIVE, headline="5 > 3 & rising")})

        self.assertNotIn("<b>\"one\"</b>", html)
        self.assertIn("&lt;b&gt;", html)
        self.assertIn("5 &gt; 3 &amp; rising", html)
        self.assertNotIn("<script>Item", html)

    def test_a_thread_without_a_narrative_says_so(self):
        registry, moving, _ = _registry_with_movement()

        html = render.render_html("2026-08-04", registry, {})

        self.assertIn("No narrative was generated", html)
        self.assertIn(moving["name"], html)

    def test_a_day_with_no_movement_renders_cleanly(self):
        registry, _, _ = _registry_with_movement()

        html = render.render_html("2026-08-05", registry, {})
        md = render.render_markdown("2026-08-05", registry, {})

        self.assertIn("No thread moved today.", html)
        self.assertIn("*No thread moved today.*", md)
        self.assertIn("0 threads moved · 2 quiet", html)

    def test_write_companion_and_archive_use_dated_filenames(self):
        registry, moving, _ = _registry_with_movement()

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            paths = render.write_companion("2026-08-04", registry,
                                           {moving["slug"]: _NARRATIVE}, output_dir=out)
            (out / "comprehension_2026-08-01.html").write_text("older", encoding="utf-8")
            (out / "comprehension_not-a-date.html").write_text("ignored", encoding="utf-8")

            entries = render.companion_archive_entries(out)
            archive = render.write_companion_archive_page(out)
            archive_html = archive.read_text(encoding="utf-8")

            self.assertEqual(paths["html_path"].name, "comprehension_2026-08-04.html")
            self.assertEqual(paths["md_path"].name, "comprehension_2026-08-04.md")

        self.assertEqual(
            entries,
            [
                ("2026-08-04", "2026-08-04 (Tuesday)", "comprehension_2026-08-04.html"),
                ("2026-08-01", "2026-08-01 (Saturday)", "comprehension_2026-08-01.html"),
            ],
        )
        self.assertIn('href="comprehension_2026-08-04.html"', archive_html)
        self.assertNotIn("not-a-date", archive_html)

    def test_empty_archive_renders_a_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(render.companion_archive_entries(Path(tmp)), [])
            html = render.generate_companion_archive_html([])

        self.assertIn("No comprehension pages yet.", html)


class DeepReadTests(unittest.TestCase):
    def test_blocked_hosts_are_never_fetched(self):
        """NYT/WSJ/FT return 403 to the scraper, so they stay abstract-only."""
        self.assertFalse(deepread.is_fetchable("https://www.nytimes.com/2026/08/04/a.html"))
        self.assertFalse(deepread.is_fetchable("https://www.wsj.com/articles/a"))
        self.assertFalse(deepread.is_fetchable(""))
        self.assertTrue(deepread.is_fetchable("https://simonwillison.net/2026/Aug/4/a/"))

    def test_extract_text_drops_noise_and_truncates(self):
        body = "First paragraph. " + "Body filler sentence. " * 30
        html = (
            "<html><body><nav>Menu menu</nav><script>var x=1;</script>"
            f"<article><p>{body}</p></article>"
            "<footer>Footer text</footer></body></html>"
        )

        text = deepread.extract_text(html)

        self.assertTrue(text.startswith("First paragraph."))
        self.assertNotIn("Menu menu", text)
        self.assertNotIn("var x=1", text)
        self.assertNotIn("Footer text", text)
        self.assertEqual(deepread.extract_text(html, max_chars=5), "First")
        self.assertEqual(deepread.extract_text(""), "")

    def test_extract_text_falls_back_to_the_body_when_article_is_empty(self):
        """Some pages wrap content in an <article> that is empty or JS-populated."""
        prose = "Real prose lives in the body. " * 30
        html = f"<html><body><article></article><div><p>{prose}</p></div></body></html>"

        text = deepread.extract_text(html)

        self.assertIn("Real prose lives in the body.", text)
        self.assertGreater(len(text), deepread.MIN_USEFUL_CHARS)

    def test_promotion_prefers_items_whose_text_can_actually_be_fetched(self):
        items_by_key = {
            "nyt": {"item_key": "nyt", "url": "https://www.nytimes.com/a", "title": "NYT"},
            "blog": {"item_key": "blog", "url": "https://simonwillison.net/b", "title": "Blog"},
        }

        selected = deepread.select_promoted(["nyt", "blog"], items_by_key, max_deep_reads=1, verbose=False)

        self.assertEqual([item["item_key"] for item in selected], ["blog"])

    def test_fetch_uses_the_cache_and_skips_blocked_hosts(self):
        cache = {"https://example.com/a": "cached body"}

        self.assertEqual(deepread.fetch_article_text("https://example.com/a", cache=cache), "cached body")
        self.assertEqual(
            deepread.fetch_article_text("https://www.nytimes.com/a", cache=cache, verbose=False), ""
        )

    def test_promotion_is_deduped_ordered_and_capped(self):
        items_by_key = {f"k{n}": {"item_key": f"k{n}", "title": f"T{n}"} for n in range(1, 5)}

        selected = deepread.select_promoted(
            ["k3", "k1", "k3", "missing", "k2"], items_by_key, max_deep_reads=2, verbose=False
        )

        self.assertEqual([item["item_key"] for item in selected], ["k3", "k1"])

    def test_prompt_marks_items_whose_text_could_not_be_fetched(self):
        entries = [
            ({"item_key": "k1", "title": "Fetched", "source": "Blog", "prose": "Summary one."}, "Body text."),
            ({"item_key": "k2", "title": "Blocked", "source": "NYT", "prose": "Summary two."}, ""),
        ]

        prompt = deepread.build_prompt(entries, thread_names={"k1": "A thread"})

        self.assertIn("Belongs to the thread: A thread", prompt)
        self.assertIn("Body text.", prompt)
        self.assertIn("Full text unavailable", prompt)
        self.assertIn("Summary two.", prompt)

    def test_context_for_items_maps_results_back_to_item_keys(self):
        items_by_key = {
            "k1": {"item_key": "k1", "title": "One", "url": "https://www.nytimes.com/a", "prose": "S1"},
            "k2": {"item_key": "k2", "title": "Two", "url": "https://www.wsj.com/b", "prose": "S2"},
        }
        payload = {"items": [
            {"index": 1, "context": "Context for one."},
            {"index": 2, "context": ""},
            {"index": 9, "context": "Out of range."},
        ]}
        client = llm.ClaudeClient(
            cache={}, cache_path=None, verbose=False,
            client=_FakeClient([_FakeResponse(json.dumps(payload))]),
        )

        contexts = deepread.context_for_items(
            ["k1", "k2"], items_by_key, client, text_cache={}, text_cache_path=None, verbose=False
        )

        self.assertEqual(contexts, {"k1": "Context for one."})

    def test_no_promoted_items_makes_no_call(self):
        fake = _FakeClient([])
        client = llm.ClaudeClient(cache={}, cache_path=None, verbose=False, client=fake)

        self.assertEqual(deepread.context_for_items([], {}, client, verbose=False), {})
        self.assertEqual(fake.messages.requests, [])

    def test_thread_names_cover_only_the_given_date(self):
        registry, moving, _ = _registry_with_movement()

        names = deepread.thread_names_for(registry, "2026-08-04")

        self.assertEqual(names, {"k2": moving["name"]})


def _digest_payload():
    return {
        "digest_date": "2026-08-04",
        "content_date": "2026-08-03",
        "nyt_note": "",
        "sections": {
            "hn": [{
                "title": "An HN story", "url": "https://example.com/a", "item_key": "https://example.com/a",
                "source": "HackerNews", "outlet": "HN", "score": 100, "comments": 5,
                "date": "2026-08-03", "discussion_overview": "Commenters argue.",
            }],
            "nyt_wsj": [{
                "title": "A news story", "url": "https://nytimes.com/b", "item_key": "https://nytimes.com/b",
                "source": "NYT Business", "outlet": "NYT", "section": "Business", "topic_tag": "Business",
                "score": 80.0, "date": "2026-08-03", "abstract": "The abstract.",
                "article_overview": "The overview.",
            }],
            "research": [{
                "title": "A paper", "url": "https://ide.mit.edu/c", "item_key": "https://ide.mit.edu/c",
                "source": "MIT IDE", "category": "research", "date": "2026-08-02",
            }],
            "blogs": [{
                "title": "A post", "url": "https://simonwillison.net/d", "item_key": "https://simonwillison.net/d",
                "source": "Simon Willison", "category": "tech", "date": "2026-08-03",
            }],
            "reddit": [], "linkedin": [],
        },
    }


class RepublishTests(unittest.TestCase):
    def test_rehydrate_recombines_research_into_blogs(self):
        data = republish.rehydrate_data(_digest_payload())

        self.assertEqual([item["title"] for item in data["blogs"]], ["A paper", "A post"])
        self.assertEqual(len(data["hn"]), 1)
        self.assertEqual(data["reddit"], [])

    def test_rehydrated_data_renders_the_same_sections_as_the_digest(self):
        data = republish.rehydrate_data(_digest_payload())

        sections = daily_digest.build_sections(data, daily_digest.DEFAULT_CONFIG["settings"])

        self.assertEqual([item["title"] for item in sections["research"]], ["A paper"])
        self.assertEqual([item["title"] for item in sections["blogs"]], ["A post"])

    def test_inject_context_attaches_notes_by_item_key(self):
        data = republish.rehydrate_data(_digest_payload())

        attached = republish.inject_context(data, {
            "https://example.com/a": "HN context.",
            "https://simonwillison.net/d": "Blog context.",
            "https://unmatched/x": "Ignored.",
        })

        self.assertEqual(attached, 2)
        self.assertEqual(data["hn"][0]["context_note"], "HN context.")
        self.assertEqual(data["blogs"][1]["context_note"], "Blog context.")
        self.assertNotIn("context_note", data["nyt_wsj"][0])
        self.assertEqual(republish.inject_context(data, {}), 0)

    def test_context_renders_in_both_html_and_markdown(self):
        data = republish.rehydrate_data(_digest_payload())
        republish.inject_context(data, {"https://example.com/a": "The background it assumes."})

        html = daily_digest.generate_html(dt.date(2026, 8, 3), data)
        md = daily_digest.generate_markdown(dt.date(2026, 8, 3), data)

        self.assertIn("<summary>Context</summary>", html)
        self.assertIn("The background it assumes.", html)
        self.assertIn("**Context:** The background it assumes.", md)
        # Overview keeps its own slot — Context is an addition, not a replacement.
        self.assertIn("Commenters argue.", html)

    def test_no_context_leaves_the_digest_untouched(self):
        data = republish.rehydrate_data(_digest_payload())

        html = daily_digest.generate_html(dt.date(2026, 8, 3), data)

        self.assertNotIn("<summary>Context</summary>", html)

    def test_rerender_writes_dated_files_and_the_index_for_the_latest_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_dir, md_dir = Path(tmp) / "html", Path(tmp) / "md"
            html_dir.mkdir()
            md_dir.mkdir()
            index = Path(tmp) / "index.html"
            (html_dir / "digest_2026-08-04.html").write_text("stale", encoding="utf-8")

            written = republish.rerender_digest(
                _digest_payload(), {"https://example.com/a": "The context."},
                daily_html_dir=html_dir, daily_md_dir=md_dir, index_path=index, verbose=False,
            )

            self.assertEqual(written["attached"], 1)
            self.assertIn("The context.", written["html_path"].read_text(encoding="utf-8"))
            self.assertIn("**Context:** The context.", written["md_path"].read_text(encoding="utf-8"))
            self.assertIn("The context.", index.read_text(encoding="utf-8"))

    def test_rerender_of_an_older_day_leaves_the_index_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_dir, md_dir = Path(tmp) / "html", Path(tmp) / "md"
            html_dir.mkdir()
            md_dir.mkdir()
            index = Path(tmp) / "index.html"
            index.write_text("current index", encoding="utf-8")
            (html_dir / "digest_2026-08-04.html").write_text("older", encoding="utf-8")
            (html_dir / "digest_2026-08-09.html").write_text("newest", encoding="utf-8")

            written = republish.rerender_digest(
                _digest_payload(), {"https://example.com/a": "The context."},
                daily_html_dir=html_dir, daily_md_dir=md_dir, index_path=index, verbose=False,
            )

            self.assertNotIn("index_path", written)
            self.assertEqual(index.read_text(encoding="utf-8"), "current index")

    def test_rerender_is_skipped_when_nothing_attaches(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_dir, md_dir = Path(tmp) / "html", Path(tmp) / "md"
            html_dir.mkdir()
            md_dir.mkdir()

            written = republish.rerender_digest(
                _digest_payload(), {}, daily_html_dir=html_dir, daily_md_dir=md_dir, verbose=False
            )

            self.assertEqual(written, {})
            self.assertEqual(list(html_dir.iterdir()), [])

    def test_rerender_needs_a_usable_content_date(self):
        payload = _digest_payload()
        payload.pop("content_date")

        self.assertEqual(
            republish.rerender_digest(payload, {"https://example.com/a": "x"}, verbose=False), {}
        )


_ESSAY = {
    "headline": "800V racks stopped being theoretical",
    "what_happened": "First paragraph.\n\nSecond paragraph.",
    "what_was_noise": "The noise paragraph.",
    "what_to_watch": "The watch paragraph.",
}


class WeeklyTests(unittest.TestCase):
    def test_window_is_seven_inclusive_days_ending_on_the_date(self):
        dates = weekly.week_dates("2026-08-04")

        self.assertEqual(len(dates), 7)
        self.assertEqual(dates[0], "2026-07-29")
        self.assertEqual(dates[-1], "2026-08-04")
        self.assertEqual(weekly.week_dates("not-a-date"), [])

    def test_window_covers_threads_that_moved_and_skips_merged_ones(self):
        registry, moving, quiet = _registry_with_movement()
        dead = store.create_thread(registry, "Merged away", as_of="2026-08-01")
        store.record_item(registry, dead["slug"], {"item_key": "d1", "title": "D1"}, as_of="2026-08-03")
        store.merge_thread(registry, dead["slug"], moving["slug"], as_of="2026-08-03")

        active = weekly.threads_in_window(registry, weekly.week_dates("2026-08-04"))

        self.assertEqual([thread["slug"] for thread, _ in active], [moving["slug"]])
        self.assertNotIn(quiet["slug"], [thread["slug"] for thread, _ in active])

    def test_prompt_carries_daily_headlines_and_names_quiet_threads(self):
        registry, moving, quiet = _registry_with_movement()
        narratives = narrative.record_narratives(
            narrative.load_narratives(Path("/nonexistent")), "2026-08-04", {moving["slug"]: _NARRATIVE}
        )

        prompt = weekly.build_prompt(registry, narratives, weekly.week_dates("2026-08-04"))

        self.assertIn("2026-08-04 — " + _NARRATIVE["headline"], prompt)
        self.assertIn("what changed: " + _NARRATIVE["what_changed"], prompt)
        self.assertIn("Second move", prompt)
        self.assertIn("stayed quiet all week", prompt)
        self.assertIn(quiet["name"], prompt)
        # Earlier history is counted, not dumped into the prompt.
        self.assertIn("1 earlier item(s)", prompt)

    def test_synthesis_stamps_the_window_and_model(self):
        registry, _, _ = _registry_with_movement()
        client = llm.ClaudeClient(
            cache={}, cache_path=None, verbose=False, model="claude-opus-5",
            client=_FakeClient([_FakeResponse(json.dumps(_ESSAY))]),
        )

        essay = weekly.synthesize_week(registry, {"dates": {}}, "2026-08-04", client)

        self.assertEqual(essay["headline"], _ESSAY["headline"])
        self.assertEqual(essay["week_start"], "2026-07-29")
        self.assertEqual(essay["week_end"], "2026-08-04")
        self.assertEqual(essay["model"], "claude-opus-5")

    def test_a_week_with_no_movement_makes_no_call(self):
        registry, _, _ = _registry_with_movement()
        fake = _FakeClient([])
        client = llm.ClaudeClient(cache={}, cache_path=None, verbose=False, client=fake)

        self.assertIsNone(weekly.synthesize_week(registry, {"dates": {}}, "2026-09-30", client))
        self.assertEqual(fake.messages.requests, [])

    def test_weeklies_round_trip_per_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly.json"
            data = weekly.record_week(weekly.load_weeklies(path), "2026-08-04", _ESSAY)
            weekly.save_weeklies(data, path)

            reloaded = weekly.load_weeklies(path)

        self.assertEqual(weekly.week_for(reloaded, "2026-08-04")["headline"], _ESSAY["headline"])
        self.assertEqual(weekly.week_for(reloaded, "2026-01-01"), {})

    def test_the_weekly_model_defaults_to_opus(self):
        self.assertEqual(weekly.default_weekly_model({}), "claude-opus-5")
        self.assertEqual(weekly.default_weekly_model({"weekly_model": "claude-sonnet-5"}), "claude-sonnet-5")


class WeeklyRenderTests(unittest.TestCase):
    def test_html_splits_paragraphs_and_labels_the_sections(self):
        essay = dict(_ESSAY, week_start="2026-07-29", week_end="2026-08-04", model="claude-opus-5")

        html = render.render_weekly_html(essay)

        self.assertIn("<title>The week in AI — Tuesday, August 4, 2026</title>", html)
        self.assertIn("What happened", html)
        self.assertIn("What was noise", html)
        self.assertIn("What to watch", html)
        self.assertIn("<p>First paragraph.</p>", html)
        self.assertIn("<p>Second paragraph.</p>", html)
        self.assertIn("2026-07-29 to 2026-08-04", html)

    def test_markdown_carries_the_same_sections(self):
        essay = dict(_ESSAY, week_start="2026-07-29", week_end="2026-08-04", model="claude-opus-5")

        md = render.render_weekly_markdown(essay)

        self.assertIn("# The week in AI — Tuesday, August 4, 2026", md)
        self.assertIn(f"**{_ESSAY['headline']}**", md)
        self.assertIn("### What was noise", md)
        self.assertIn(_ESSAY["what_to_watch"], md)

    def test_an_empty_essay_renders_a_placeholder(self):
        html = render.render_weekly_html({"week_start": "a", "week_end": "b"})

        self.assertIn("No synthesis was generated", html)

    def test_the_archive_lists_weekly_essays_separately(self):
        essay = dict(_ESSAY, week_start="2026-07-29", week_end="2026-08-04", model="claude-opus-5")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            paths = render.write_weekly(essay, output_dir=out)
            entries = render.weekly_archive_entries(out)
            archive = render.write_companion_archive_page(out)
            html = archive.read_text(encoding="utf-8")

            self.assertEqual(paths["html_path"].name, "weekly_2026-08-04.html")
            self.assertEqual(paths["md_path"].name, "weekly_2026-08-04.md")

        self.assertEqual(entries, [("2026-08-04", "2026-08-04 (Tuesday)", "weekly_2026-08-04.html")])
        self.assertIn("Weekly synthesis", html)
        self.assertIn("Week ending 2026-08-04 (Tuesday)", html)
        self.assertIn("Daily, newest first", html)

    def test_the_archive_omits_the_weekly_section_when_there_are_none(self):
        html = render.generate_companion_archive_html([], [])

        self.assertNotIn("Weekly synthesis", html)


class RunCliTests(unittest.TestCase):
    def test_backfill_range_is_inclusive(self):
        self.assertEqual(
            run.parse_date_range("2026-08-01..2026-08-04"),
            ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"],
        )
        self.assertEqual(run.parse_date_range("2026-08-01..2026-08-01"), ["2026-08-01"])

    def test_bad_backfill_ranges_have_clear_errors(self):
        for value, message in (
            ("2026-08-01", "START..END"),
            ("08/01/2026..08/04/2026", "YYYY-MM-DD"),
            ("2026-08-04..2026-08-01", "precede"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                run.parse_date_range(value)

    def test_settings_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"settings": {"comprehension": {"effort": "high"}}}), encoding="utf-8")

            settings = run.load_settings(path)

        self.assertEqual(settings["effort"], "high")
        self.assertEqual(settings["model"], llm.DEFAULT_DAILY_MODEL)
        self.assertEqual(settings["weekly_model"], llm.DEFAULT_WEEKLY_MODEL)

    def test_shipped_config_selects_current_model_ids(self):
        settings = run.load_settings()

        self.assertEqual(settings["model"], "claude-sonnet-5")
        self.assertEqual(settings["weekly_model"], "claude-opus-5")

    def test_missing_profile_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run.load_profile(Path(tmp) / "absent.md"), "")

    def test_the_committed_profile_template_covers_the_sections_that_matter(self):
        """The real profile is gitignored, so the template is what a clone gets."""
        template = run.load_profile(Path(__file__).resolve().parent.parent / "config" / "reader_profile.example.md")

        self.assertIn("Reader profile", template)
        for heading in ("## Role", "## Solid on", "## Hazy on", "## Entity watchlist", "## Guardrails"):
            self.assertIn(heading, template)


if __name__ == "__main__":
    unittest.main()
