# Daily Digest

Your personal morning news digest, generated daily at **6:00 AM**.

---

## Output Files (generated each morning)

| File | Description |
|------|-------------|
| `digest_YYYY-MM-DD.html` | Styled HTML digest for the day |
| `digest_YYYY-MM-DD.md` | Clean markdown version |
| `index.html` | Always = latest digest (served by GitHub Pages) |
| `hn_archive.md` | Running HN top-10 table — appended daily |
| `hn_archive_data.json` | Raw HN archive data |
| `dd_archive.md` | Running full-digest table — appended daily |
| `dd_archive_data.json` | Raw digest archive data |

---

## Quick Start

```bash
cd ~/Documents/daily_digest/Daily\ Digest
pip3 install requests feedparser beautifulsoup4 lxml --break-system-packages
python3 daily_digest.py
```

---

## GitHub Pages Setup (phone web app)

Run once to get a persistent URL accessible from any device:

```bash
bash setup_github_pages.sh
```

The script walks you through everything. When done:
- Your digest URL: `https://YOURUSERNAME.github.io/REPONAME/`
- Add it to your iPhone home screen: Safari → Share → **Add to Home Screen**
- Every morning at 6 AM, `index.html` is updated and pushed automatically

Then enable in `config.json`:
```json
"github_pages": { "enabled": true }
```

---

## HN Historical Archive

Run once to seed the archive with the past year of HN data (~6 min):
```bash
python3 hn_historical.py
```

For a quick 7-day test first:
```bash
python3 hn_archive_sample.py
```

After the initial seed, `daily_digest.py` appends each new day automatically.

---

## Digest Structure

**Alpha**
- HN, NYT, WSJ, MIT IDE, MIT Shaping Work

**Beta**
- Security: Krebs on Security, Troy Hunt
- Tech: Simon Willison, Dan Luu, Tonsky.me, Paul Graham, Gwern.net, Lemire.me
- Strategy: MIT Sloan Review, Stratechery, Daring Fireball, Rachel by the Bay

---

## Install all dependencies

```bash
pip3 install requests feedparser beautifulsoup4 lxml gspread google-auth --break-system-packages
```
