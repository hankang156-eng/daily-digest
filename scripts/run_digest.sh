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

# Wait for network/DNS to be ready. LaunchAgent fires on system wake before
# WiFi/DNS settles -> all fetches fail with "nodename nor servname provided"
# and the run silently preserves stale files. Probe HN as a canary; up to 60s.
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if curl -fsS --max-time 4 -o /dev/null https://news.ycombinator.com 2>/dev/null; then
        log "  [Network] ready after $((i*5))s"
        break
    fi
    [ "$i" -eq 12 ] && log "  [Network] still not ready after 60s; proceeding anyway"
    sleep 5
done

# The digest date is the user-facing --date if given, else today. The
# comprehension pass keys off the same date so both halves describe one day.
DIGEST_DATE=""
PREV_ARG=""
for arg in "$@"; do
    case "$arg" in
        --date=*) DIGEST_DATE="${arg#--date=}" ;;
    esac
    if [ "$PREV_ARG" = "--date" ]; then
        DIGEST_DATE="$arg"
    fi
    PREV_ARG="$arg"
done
if [ -z "$DIGEST_DATE" ]; then
    DIGEST_DATE="$(date +%Y-%m-%d)"
fi

# Ensure dependencies are installed (fast no-op if already present)
log ""
log "  [1/4] Checking Python dependencies..."
"$PYTHON" -m pip install -r "$REPO_ROOT/requirements.txt" -q 2>&1 | tee -a "$LOG_FILE"
PIP_STATUS=${PIPESTATUS[0]}
if [ "$PIP_STATUS" -ne 0 ]; then
    log "  [Dependencies] pip install failed with exit code $PIP_STATUS"
    exit "$PIP_STATUS"
fi

cd "$REPO_ROOT"
log ""
log "  [2/4] Running src/daily_digest.py..."
PYTHONUNBUFFERED=1 "$PYTHON" src/daily_digest.py "$@" 2>&1 | tee -a "$LOG_FILE"
DIGEST_STATUS=${PIPESTATUS[0]}

# Comprehension runs after the digest and must never fail the run: the digest's
# own outputs are already written, and its exit code is what this script returns.
log ""
if [ "$DIGEST_STATUS" -eq 0 ]; then
    log "  [3/4] Running comprehension pass for $DIGEST_DATE..."
    PYTHONUNBUFFERED=1 "$PYTHON" -m src.comprehension.run --date "$DIGEST_DATE" 2>&1 | tee -a "$LOG_FILE"
    COMPREHENSION_STATUS=${PIPESTATUS[0]}
    if [ "$COMPREHENSION_STATUS" -ne 0 ]; then
        log "  [Comprehension] exited $COMPREHENSION_STATUS; the digest above is unaffected."
    fi

    # Weekly synthesis on Sundays. Remove this block to make it manual-only.
    if [ "$(date +%u)" -eq 7 ]; then
        log ""
        log "  [3b/4] Weekly synthesis for the week ending $DIGEST_DATE..."
        PYTHONUNBUFFERED=1 "$PYTHON" -m src.comprehension.run --weekly --date "$DIGEST_DATE" 2>&1 | tee -a "$LOG_FILE"
        WEEKLY_STATUS=${PIPESTATUS[0]}
        if [ "$WEEKLY_STATUS" -ne 0 ]; then
            log "  [Comprehension] weekly synthesis exited $WEEKLY_STATUS; not fatal."
        fi
    fi
else
    log "  [3/4] Skipping comprehension because the digest failed."
fi

log ""
if [ "$DIGEST_STATUS" -eq 0 ]; then
    log "  [4/4] Daily digest completed successfully."
else
    log "  [4/4] Daily digest failed with exit code $DIGEST_STATUS."
fi
log "  Run finished: $(date)"
exit "$DIGEST_STATUS"
