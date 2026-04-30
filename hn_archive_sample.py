#!/usr/bin/env python3
"""
Quick test: generates hn_archive_data.json + hn_archive.md from
the last 7 days of real HN data.
Run this first to verify output looks good before the full 365-day build.
"""
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "hn_historical.py", "--days", "7", "--top", "10", "--refresh"],
    cwd=str(__import__('pathlib').Path(__file__).parent)
)
sys.exit(result.returncode)
