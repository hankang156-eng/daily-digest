# Daily Digest

Personal morning news digest, generated daily at 6:00 AM.

Everything lives in:

```bash
/Users/michellekang/Documents/daily_digest
```

## Quick Start

```bash
cd /Users/michellekang/Documents/daily_digest
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements.txt
.venv/bin/python3 daily_digest.py
```

## Outputs

| File | Description |
|------|-------------|
| `digest_YYYY-MM-DD.html` | Styled web/mobile digest |
| `digest_YYYY-MM-DD.md` | Obsidian-friendly markdown digest |
| `index.html` | Exact copy of the latest digest for GitHub Pages |
| `hn_archive.md` | Running HN top-10 markdown archive |
| `hn_archive.xlsx` | Excel workbook generated from `hn_archive_data.json` |
| `hn_archive_data.json` | Raw HN archive data |
| `dd_archive.md` | Running digest article archive |
| `dd_archive.xlsx` | Excel workbook generated from `dd_archive_data.json` |
| `dd_archive_data.json` | Raw digest archive data |

## Digest Structure

**☕️ Espresso**
- HackerNews top 5
- Current Events: 3 NYT + 2 WSJ articles
- Up to 3 opinion pieces
- MIT IDE + MIT Shaping Work updates
- LinkedIn placeholder link for Rama's activity

**📚 Lungo**
- HackerNews #6-#12
- 5 additional NYT/WSJ headlines
- Up to 3 additional opinion pieces
- Security: Krebs on Security, Troy Hunt
- Tech: Simon Willison, Dan Luu, Tonsky.me, Paul Graham, Gwern.net, Lemire.me, Neal.fun
- Strategy: MIT Sloan Review, Daring Fireball, Rachel by the Bay, Shkspr.mobi

## GitHub Pages

Run once:

```bash
bash setup_github_pages.sh
```

Then follow the printed instructions to create the GitHub repo, add the remote, enable Pages, and add the URL to your iPhone home screen.

Enable automatic push in `config.json`:

```json
"github_pages": { "enabled": true }
```

## Daily macOS Automation

Run once:

```bash
bash setup_launchagent.sh
```

Test manually:

```bash
bash run_digest.sh
tail -100 digest.log
```

Uninstall:

```bash
launchctl unload ~/Library/LaunchAgents/com.michelle.dailydigest.plist
rm ~/Library/LaunchAgents/com.michelle.dailydigest.plist
```

## HN Historical Seed

Seed up to the past year:

```bash
.venv/bin/python3 hn_historical.py --days 365 --top 10 --refresh
```

Quick 7-day test:

```bash
.venv/bin/python3 hn_archive_sample.py
```

After this, `daily_digest.py` appends future daily HN archive entries.

By default, the historical seeder uses Algolia `tags=story` and sorts by points. The older `tags=front_page` mode is available with `--tag front_page`, but it is sparse for historical dates and usually will not produce a complete top-10 archive.

## Dependencies

```bash
pip install requests feedparser beautifulsoup4 lxml
```

The scripts degrade gracefully if optional packages are missing, but installing all dependencies gives the full digest.
