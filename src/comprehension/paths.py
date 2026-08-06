"""Repo-relative paths for the comprehension layer.

Derived from __file__ so the checkout can move (including into a git worktree).
"""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent

# Inputs
DIGEST_JSON_DIR = REPO_ROOT / "output" / "digest_json"
DAILY_MD_DIR = REPO_ROOT / "output" / "daily_md"
CONFIG_FILE = REPO_ROOT / "config" / "config.json"
READER_PROFILE_FILE = REPO_ROOT / "config" / "reader_profile.md"

# Persistent state
DATA_DIR = REPO_ROOT / "data" / "comprehension"
THREADS_FILE = DATA_DIR / "threads.json"
NARRATIVES_FILE = DATA_DIR / "narratives.json"
WEEKLY_FILE = DATA_DIR / "weekly.json"
MARKS_FILE = DATA_DIR / "marks.json"
MARKS_ARCHIVE_DIR = DATA_DIR / "marks_archive"

# Outputs and caches
OUTPUT_DIR = REPO_ROOT / "output" / "comprehension"
DAILY_HTML_DIR = REPO_ROOT / "output" / "daily_html"
RANKER_OUTPUT_DIR = REPO_ROOT / "output" / "ranker_diagnostics"
CACHE_FILE = RANKER_OUTPUT_DIR / "comprehension_cache.json"

# Where the digest page's "Save marks" download lands.
DEFAULT_MARKS_DOWNLOAD_DIR = Path.home() / "Downloads"
