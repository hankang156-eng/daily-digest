#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Daily Digest — runner script
# Run manually from Terminal or by an automation.
# Uses the virtual environment python if present, else system python3.
# ─────────────────────────────────────────────────────────────────────────────

set -o pipefail

SCRIPT_DIR="/Users/michellekang/Documents/daily_digest"
VENV_PYTHON="/Users/michellekang/Documents/daily_digest/.venv/bin/python3"
LOG_FILE="$SCRIPT_DIR/digest.log"

# Rotate log: keep only the last 500 lines
if [ -f "$LOG_FILE" ]; then
    tail -500 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

log() {
    echo "$@" | tee -a "$LOG_FILE"
}

log ""
log "========================================"
log "  Daily Digest run started: $(date)"
log "  Workspace: $SCRIPT_DIR"
log "  Log file:  $LOG_FILE"
log "========================================"

# Pick python
if [ -f "$VENV_PYTHON" ]; then
    PYTHON="$VENV_PYTHON"
else
    PYTHON="/usr/bin/python3"
fi
log "  Python: $PYTHON"

# Ensure dependencies are installed (fast no-op if already present)
log ""
log "  [1/3] Checking Python dependencies..."
"$PYTHON" -m pip install requests feedparser beautifulsoup4 lxml -q 2>&1 | tee -a "$LOG_FILE"
PIP_STATUS=${PIPESTATUS[0]}
if [ "$PIP_STATUS" -ne 0 ]; then
    log "  [Dependencies] pip install failed with exit code $PIP_STATUS"
    exit "$PIP_STATUS"
fi

cd "$SCRIPT_DIR"
log ""
log "  [2/3] Running daily_digest.py..."
PYTHONUNBUFFERED=1 "$PYTHON" daily_digest.py 2>&1 | tee -a "$LOG_FILE"
DIGEST_STATUS=${PIPESTATUS[0]}

log ""
if [ "$DIGEST_STATUS" -eq 0 ]; then
    log "  [3/3] Daily digest completed successfully."
else
    log "  [3/3] Daily digest failed with exit code $DIGEST_STATUS."
fi
log "  Run finished: $(date)"
exit "$DIGEST_STATUS"
