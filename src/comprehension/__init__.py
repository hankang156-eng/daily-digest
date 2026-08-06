"""Comprehension layer: turns the daily digest into tracked storylines.

The digest answers "what should I read". This package answers "what does it
mean" by filing each day's items into persistent threads, so a given day's news
reads as an update to a story already understood rather than a fresh thing to
learn.

Input is the JSON contract written by src/daily_digest.dump_digest_json (or, for
backfill, the archived Markdown digests). Nothing here reads the Merge4 vault.
"""
