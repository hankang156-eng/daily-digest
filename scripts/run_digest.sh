#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Daily Digest — runner script
# Run manually from Terminal or by an automation.
# Uses the virtual environment python if present, else system python3.
#
# The LaunchAgent passes --scheduled (consumed here, never forwarded). A
# scheduled run exits early when today's digest is already built and published,
# and gives up without touching anything when the network never comes up, so
# the hourly slots in the plist act as retries.
# ─────────────────────────────────────────────────────────────────────────────

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python3"
LOG_FILE="$REPO_ROOT/data/digest_archives/digest.log"

# Exit code for "no network, try again at the next slot" (EX_TEMPFAIL).
EXIT_RETRY_LATER=75

# Keep the Mac awake for the whole run. When the Mac is asleep at 6:00, launchd
# fires the job during a ~45s background maintenance wake; without a power
# assertion the Mac drops back to sleep mid-run and the process freezes for
# hours, resuming into a network that is not up yet. -w ties the assertion to
# this shell, so it is released when the run exits.
if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i -s -w $$ &
fi

SCHEDULED=0
FORWARD_ARGS=()
for arg in "$@"; do
    if [ "$arg" = "--scheduled" ]; then
        SCHEDULED=1
    else
        FORWARD_ARGS+=("$arg")
    fi
done
set -- "${FORWARD_ARGS[@]}"

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

# Built, companion page written, and nothing left unpushed.
digest_is_complete() {
    local day="$1" ahead
    [ -f "$REPO_ROOT/output/daily_html/digest_$day.html" ] || return 1
    [ -f "$REPO_ROOT/output/comprehension/comprehension_$day.html" ] || return 1
    ahead="$(git -C "$REPO_ROOT" rev-list --count origin/main..HEAD 2>/dev/null)" || return 1
    [ "$ahead" = "0" ]
}

# Later hourly slots are no-ops once the day is done. Stdout only (the
# LaunchAgent's /tmp log), so the digest log is not padded with skips.
if [ "$SCHEDULED" -eq 1 ] && digest_is_complete "$DIGEST_DATE"; then
    echo "  [Schedule] $(date): $DIGEST_DATE is already built and published; nothing to do."
    exit 0
fi

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
if [ "$SCHEDULED" -eq 1 ]; then
    log "  Trigger:   scheduled (LaunchAgent)"
fi
log "========================================"

# Pick python
if [ -f "$VENV_PYTHON" ]; then
    PYTHON="$VENV_PYTHON"
else
    PYTHON="/usr/bin/python3"
fi
log "  Python: $PYTHON"

# Wait for network/DNS. Wi-Fi and DNS often lag a wake, and without them every
# fetch fails with "nodename nor servname provided" and the run produces
# nothing. Probe HN as a canary. A scheduled run waits longer and, if the
# network never comes up, stops so the next hourly slot can retry; a manual
# run proceeds anyway, as before.
NETWORK_WAIT=60
if [ "$SCHEDULED" -eq 1 ]; then
    NETWORK_WAIT=300
fi
NETWORK_READY=0
SECONDS=0
while :; do
    if curl -fsS --max-time 4 -o /dev/null https://news.ycombinator.com 2>/dev/null; then
        NETWORK_READY=1
        log "  [Network] ready after ${SECONDS}s"
        break
    fi
    [ "$SECONDS" -ge "$NETWORK_WAIT" ] && break
    sleep 5
done
if [ "$NETWORK_READY" -eq 0 ]; then
    if [ "$SCHEDULED" -eq 1 ]; then
        log "  [Network] not ready after ${SECONDS}s; skipping this slot. The next hourly slot will retry."
        log "  Run finished: $(date)"
        exit "$EXIT_RETRY_LATER"
    fi
    log "  [Network] still not ready after ${SECONDS}s; proceeding anyway"
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

    # Publish again. The digest published inside step [2/4], before the companion
    # page existed, so without this the digest links to a page that only goes live
    # with tomorrow's push. Also never fatal.
    log ""
    log "  [3c/4] Publishing comprehension outputs..."
    PYTHONUNBUFFERED=1 "$PYTHON" src/daily_digest.py --publish-only ${DIGEST_DATE:+--date "$DIGEST_DATE"} 2>&1 | tee -a "$LOG_FILE"
    REPUBLISH_STATUS=${PIPESTATUS[0]}
    if [ "$REPUBLISH_STATUS" -ne 0 ]; then
        log "  [Comprehension] publish step exited $REPUBLISH_STATUS; not fatal."
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
if [ "$SCHEDULED" -eq 1 ] && ! digest_is_complete "$DIGEST_DATE"; then
    log "  [Schedule] $DIGEST_DATE is not fully built and published yet; the next hourly slot will retry."
fi
log "  Run finished: $(date)"
exit "$DIGEST_STATUS"
