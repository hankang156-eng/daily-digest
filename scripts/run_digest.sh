#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Daily Digest — runner script
# Run manually from Terminal or by an automation.
# Uses the virtual environment python if present, else system python3.
# ─────────────────────────────────────────────────────────────────────────────

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python3"
LOG_FILE="$REPO_ROOT/data/digest_archives/digest.log"

# Load API keys from a gitignored .env at the repo root, if present.
# Covers manual and scheduled (LaunchAgent) runs since both invoke this script.
# Expected keys: GEMINI_API_KEY, NYT_API_KEY, ANTHROPIC_API_KEY.
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env"
    set +a
fi

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
log "  Workspace: $REPO_ROOT"
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

cd "$REPO_ROOT"
log ""
log "  [2/3] Running src/daily_digest.py..."
PYTHONUNBUFFERED=1 "$PYTHON" src/daily_digest.py "$@" 2>&1 | tee -a "$LOG_FILE"
DIGEST_STATUS=${PIPESTATUS[0]}

log ""
if [ "$DIGEST_STATUS" -eq 0 ]; then
    log "  [3/3] Daily digest completed successfully."
else
    log "  [3/3] Daily digest failed with exit code $DIGEST_STATUS."
fi
log "  Run finished: $(date)"
exit "$DIGEST_STATUS"
