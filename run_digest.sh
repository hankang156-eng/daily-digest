#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Daily Digest — runner script
# Called by the macOS LaunchAgent every morning at 6 AM.
# Uses the virtual environment python if present, else system python3.
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="/Users/michellekang/Documents/daily_digest"
VENV_PYTHON="/Users/michellekang/Documents/daily_digest/.venv/bin/python3"
LOG_FILE="$SCRIPT_DIR/digest.log"

# Rotate log: keep only the last 500 lines
if [ -f "$LOG_FILE" ]; then
    tail -500 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

echo "" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"
echo "  Run started: $(date)" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# Pick python
if [ -f "$VENV_PYTHON" ]; then
    PYTHON="$VENV_PYTHON"
else
    PYTHON="/usr/bin/python3"
fi

# Ensure dependencies are installed (fast no-op if already present)
"$PYTHON" -m pip install requests feedparser beautifulsoup4 lxml -q >> "$LOG_FILE" 2>&1

cd "$SCRIPT_DIR"
"$PYTHON" daily_digest.py >> "$LOG_FILE" 2>&1

echo "  Run finished: $(date)" >> "$LOG_FILE"
