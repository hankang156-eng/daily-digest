#!/usr/bin/env python3
"""
Quick test: generates hn_archive.html + hn_archive.xlsx from
the LAST 7 DAYS of real HN data (takes ~10 seconds).
Run this first to verify output looks good before the full 365-day build.
"""
import subprocess, sys
result = subprocess.run(
    [sys.executable, "hn_historical.py", "--days", "7"],
    cwd=str(__import__('pathlib').Path(__file__).parent)
)
sys.exit(result.returncode)
