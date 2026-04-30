#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Daily Digest — macOS LaunchAgent Setup
# Installs a native 6 AM scheduler that runs with your full internet access.
# Run once: bash setup_launchagent.sh
# ─────────────────────────────────────────────────────────────────────────────

PLIST_NAME="com.michelle.dailydigest"
PLIST_SRC="/Users/michellekang/Documents/daily_digest/com.michelle.dailydigest.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
RUNNER="/Users/michellekang/Documents/daily_digest/run_digest.sh"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Daily Digest — macOS Scheduler Setup              ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Make runner executable
chmod +x "$RUNNER"
echo "  ✓ Made run_digest.sh executable"

# Create LaunchAgents dir if missing
mkdir -p "$HOME/Library/LaunchAgents"

# Copy plist to LaunchAgents
cp "$PLIST_SRC" "$PLIST_DEST"
echo "  ✓ Installed plist to ~/Library/LaunchAgents/"

# Load the job with the modern per-user bootstrap command
launchctl bootout "gui/$(id -u)" "$PLIST_DEST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
echo "  ✓ Loaded LaunchAgent"

echo ""
echo "  ─────────────────────────────────────────────────────"
echo "  Done! Your digest will run every day at 6:00 AM."
echo ""
echo "  To test it right now:"
echo "    bash run_digest.sh"
echo ""
echo "  To view the log:"
echo "    cat digest.log"
echo ""
echo "  To uninstall the scheduler:"
echo "    launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/$PLIST_NAME.plist"
echo "    rm ~/Library/LaunchAgents/$PLIST_NAME.plist"
echo ""
echo "  If launchd reports 'Operation not permitted' for the Documents folder,"
echo "  grant Full Disk Access to /bin/bash or move this workspace outside"
echo "  ~/Documents, Desktop, and Downloads."
echo "  ─────────────────────────────────────────────────────"
echo ""
