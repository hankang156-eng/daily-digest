import datetime as dt
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import daily_digest


def _completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class PushToGithubTests(unittest.TestCase):
    """A run that commits and then loses the network must not strand the commit."""

    CONFIG = {"github_pages": {"enabled": True}, "settings": {}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "index.html").write_text("x", encoding="utf-8")
        patches = [
            mock.patch.object(daily_digest, "REPO_ROOT", self.root),
            mock.patch.object(
                daily_digest,
                "github_pages_publish_files",
                return_value=["index.html", "output/daily_html/digest_2026-11-08.html"],
            ),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("DAILY_DIGEST_SKIP_GITHUB", None)
        self.addCleanup(self._tmp.cleanup)

    def _push(self, staged, ahead):
        git_calls = []

        def run_git(args):
            git_calls.append(args)
            return True, _completed()

        def git_quiet(args):
            if args[:2] == ["diff", "--cached"]:
                return _completed(1 if staged else 0)
            if args[:2] == ["rev-list", "--count"]:
                return _completed(0, f"{ahead}\n")
            raise AssertionError(f"unexpected git probe {args}")

        with mock.patch.object(daily_digest, "_run_git", side_effect=run_git), \
                mock.patch.object(daily_digest, "_git_quiet", side_effect=git_quiet):
            daily_digest.push_to_github(dt.date(2026, 11, 7), self.CONFIG)
        return [call[0] for call in git_calls], git_calls

    def test_pushes_a_stranded_commit_when_nothing_new_is_staged(self):
        verbs, _ = self._push(staged=False, ahead=1)
        self.assertEqual(verbs, ["add", "push"])

    def test_commits_and_pushes_new_changes(self):
        verbs, calls = self._push(staged=True, ahead=1)
        self.assertEqual(verbs, ["add", "commit", "push"])
        self.assertEqual(calls[1], ["commit", "-m", "digest: 2026-11-07"])

    def test_does_nothing_when_up_to_date(self):
        verbs, _ = self._push(staged=False, ahead=0)
        self.assertEqual(verbs, ["add"])

    def test_missing_files_are_not_passed_to_git_add(self):
        # A day with no content writes no digest file; adding it would fail the
        # whole `git add` and skip the push.
        _, calls = self._push(staged=False, ahead=0)
        self.assertEqual(calls[0], ["add", "index.html"])


class SecretRedactionTests(unittest.TestCase):
    def test_gemini_key_is_sent_as_a_header_not_in_the_url(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        with mock.patch.object(daily_digest.requests, "post", return_value=response) as post, \
                mock.patch.object(daily_digest, "_throttle_gemini"):
            daily_digest._gemini_generate("prompt", "gemini-test", "SECRET123", max_attempts=1)

        kwargs = post.call_args.kwargs
        self.assertNotIn("SECRET123", post.call_args.args[0])
        self.assertNotIn("key", kwargs.get("params") or {})
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "SECRET123")

    def test_redact_secrets_masks_query_string_keys(self):
        text = (
            "403 Client Error: Forbidden for url: "
            "https://api.nytimes.com/svc/search/v2/articlesearch.json?q=x&api-key=SECRET123&fl=web_url "
            "and https://example.com/v1?key=OTHER456"
        )
        redacted = daily_digest._redact_secrets(text)
        self.assertNotIn("SECRET123", redacted)
        self.assertNotIn("OTHER456", redacted)
        self.assertIn("api-key=REDACTED", redacted)
        self.assertIn("&fl=web_url", redacted)


if __name__ == "__main__":
    unittest.main()
