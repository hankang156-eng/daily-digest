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

## Google Sheets Setup (digest archive auto-sync)

The daily digest archive (`dd_archive`) auto-appends to a Google Sheet each morning.

### One-time setup (~10 minutes)

**Step 1 — Google Cloud project**
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g. "Daily Digest")
3. Enable two APIs:
   - [Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com)
   - [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)

**Step 2 — Service account**
1. Go to **IAM & Admin → Service Accounts → Create Service Account**
2. Name it `daily-digest` — click through the steps
3. Open the new service account → **Keys → Add Key → JSON**
4. Download the JSON file
5. Rename it `google_credentials.json` and place it in this folder

**Step 3 — Create the Google Sheet**
1. Go to [sheets.google.com](https://sheets.google.com) and create a new spreadsheet
2. Name it `Daily Digest Archive`
3. Copy the sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/**THIS_PART**/edit`

**Step 4 — Share with service account**
1. Open `google_credentials.json` and find `"client_email"` — copy that address
2. In your Google Sheet: **Share → paste that email → Editor**

**Step 5 — Configure**
Update `config.json`:
```json
"google_sheets": {
  "enabled": true,
  "sheet_id": "YOUR_SHEET_ID_HERE",
  "credentials_file": "google_credentials.json"
}
```

Install the Python dependency:
```bash
pip3 install gspread google-auth --break-system-packages
```

### Sheet structure
| Digest Date | Title | Source | Topic | Category | Pub Date | URL |

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

**⭐ Essential**
- 🔶 HackerNews top 6 (with scores + discussion links)
- 📰 Current Events — 3 NYT + 3 WSJ headlines
- 💭 Opinion & Analysis — NYT + WSJ opinion
- 🎓 MIT Research — MIT IDE + MIT Shaping Work
- 💼 LinkedIn — Ramar's recent activity (direct link)

**📚 Expanded**
- 🔶 HackerNews #7–#10
- 📰 More headlines
- 💭 More opinion
- 🔒 Security: Krebs on Security, Troy Hunt
- ⚙️ Tech: Simon Willison, Dan Luu, Tonsky.me, Paul Graham, Gwern.net, Lemire.me
- 🧠 Strategy: MIT Sloan Review, Stratechery, Daring Fireball, Rachel by the Bay

---

## Install all dependencies

```bash
pip3 install requests feedparser beautifulsoup4 lxml gspread google-auth --break-system-packages
```
