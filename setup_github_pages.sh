#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Daily Digest — GitHub Pages One-Time Setup
# Run this once from the daily_digest workspace: bash setup_github_pages.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Daily Digest — GitHub Pages Setup                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
read -p "  GitHub username: " GITHUB_USER
read -p "  New private repo name (e.g. daily-digest): " REPO_NAME
echo ""

# ── 1. Initialize git repo ───────────────────────────────────────────────────
if [ ! -d ".git" ]; then
    git init
    git checkout -b main
    echo "  ✓ Initialized git repository"
else
    echo "  ✓ Git repository already initialized"
fi

# ── 2. Create .gitignore ─────────────────────────────────────────────────────
cat > .gitignore << 'GITIGNORE'
# Secrets
config.json
google_credentials.json

# Large data files (regenerated from JSON)
hn_archive_data.json
dd_archive_data.json
digest.log

# Python cache
__pycache__/
.venv/
*.pyc
*.pyo

# macOS
.DS_Store

# Local workspace leftovers / Obsidian folder
Daily Digest/
daily_digest/
GITIGNORE
echo "  ✓ Created .gitignore"

# ── 3. Seed index.html if it doesn't exist ───────────────────────────────────
if [ ! -f "index.html" ]; then
    cat > index.html << 'HTML'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Daily Digest</title>
  <style>
    body { font-family: -apple-system, sans-serif; max-width: 500px; margin: 80px auto;
           text-align: center; color: #444; }
    h1 { color: #1a1a2e; }
  </style>
</head>
<body>
  <h1>☕ Daily Digest</h1>
  <p>Your first digest will appear here tomorrow morning at 6 AM.</p>
</body>
</html>
HTML
    echo "  ✓ Created placeholder index.html"
fi

# ── 4. Initial commit ────────────────────────────────────────────────────────
git add index.html .gitignore requirements.txt README.md \
        daily_digest.py hn_historical.py setup_github_pages.sh \
        hn_archive_sample.py run_digest.sh setup_launchagent.sh \
        com.michelle.dailydigest.plist hn_archive.md hn_archive.xlsx \
        dd_archive.md dd_archive.xlsx 2>/dev/null || true
git commit -m "initial setup" 2>/dev/null || echo "  (nothing new to commit)"

# ── 5. Instructions ──────────────────────────────────────────────────────────
echo ""
echo "  ─────────────────────────────────────────────────────"
echo "  NEXT STEPS (takes ~3 minutes):"
echo "  ─────────────────────────────────────────────────────"
echo ""
echo "  1. Create a new GitHub repo:"
echo "     https://github.com/new"
echo "     Name:       $REPO_NAME"
echo "     Visibility: Private ✓"
echo "     ⚠ Do NOT check 'Add a README' or any other init options"
echo ""
echo "  2. Connect and push (run these two commands):"
echo ""
echo "     git remote add origin https://github.com/$GITHUB_USER/$REPO_NAME.git"
echo "     git push -u origin main"
echo ""
echo "  3. Enable GitHub Pages:"
echo "     https://github.com/$GITHUB_USER/$REPO_NAME/settings/pages"
echo "     → Source: 'Deploy from a branch'"
echo "     → Branch: main  /  (root)"
echo "     → Save"
echo ""
echo "  4. Enable auto-push in config.json — change this:"
echo "     \"github_pages\": { \"enabled\": true }"
echo ""
echo "  5. Your digest URL (ready in ~2 min after step 3):"
echo "     https://$GITHUB_USER.github.io/$REPO_NAME/"
echo ""
echo "  ─────────────────────────────────────────────────────"
echo "  Add that URL to your phone's home screen:"
echo "  iPhone: Safari → Share → 'Add to Home Screen'"
echo "  Android: Chrome → ⋮ → 'Add to Home Screen'"
echo "  ─────────────────────────────────────────────────────"
echo ""
