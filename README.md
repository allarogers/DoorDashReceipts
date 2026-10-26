# The story behind this project
I found an unpleasant surprise a few weeks after moving into my new house: the pipe behind my stove had a huge crack and the leak has been going on for years undetected.  Needless to say, mold started growing behind the drywall and had to tear it all out to dry it out properly.
Long story short: my family lived mostly off DoorDash for a few months.  Lucky for us, our wonderful home insurance covered the expenses, but that also meant hours of tedious expense reporting for me.

# Problem Statement
DoorDash does not have a bulk receipt export for expense reports.  Manual work is time consuming and error prone.

# Solution
DoorDash Receipt scraper is a python application that uses **Selenium** + **Chrome** to log into DoorDash, walk your order history in a configurable date range, and for each in-range order:

1. **Adds a row to a CSV** — date, meal (breakfast / lunch / dinner from order time), vendor, total charge, order time (from the receipt when available)
2. **Saves a screenshot** of the receipt (`[date]_[total].png`)
3. **Saves a PDF** of the receipt via Chrome DevTools **Print to PDF** (same idea as Print → Save as PDF), as `[date]_[total].pdf`

> Note: DoorDash only loads **~10 orders** at a time in the list; the scraper **scrolls in steps** as it goes (after each receipt it returns to `/orders/`, so it re-applies scroll depth so deeper orders stay reachable). See `scraper.py` (around the `need_rounds = min(pos // 10, …)` logic).
---

## Before You Begin: Gather Your Paths

Before running the commands below, identify these directory paths for your machine:

| Placeholder | Description | Example (Windows) | Example (macOS) |
|---|---|---|---|
| `<PROJECT_DIR>` | Path to this cloned repository | `C:\Users\<username>\Documents\DoorDashReceipts` | `/Users/<username>/Documents/Code/DoorDashReceipts` |
| `<PARENT_DIR>` | The folder that *contains* `DoorDashReceipts` | `C:\Users\<username>\Documents` | `/Users/<username>/Documents/Code` |
| `<CHROME_PATH>` | Path to your Google Chrome executable | `C:\Program Files\Google\Chrome\Application\chrome.exe` | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| `<CHROME_DEBUG_DIR>` | Dedicated folder for debug sessions | `C:\Users\<username>\Documents\ChromeDebug` | `/Users/<username>/ChromeDebug` |

---

## Setup

1. **Python 3.10+** (tested with 3.14).

2. **Install dependencies** (no Playwright):

   **Windows (PowerShell / Command Prompt):**
   ```powershell
   cd "<PROJECT_DIR>"
   py -m pip install -r requirements.txt
   ```

   **macOS / Linux (Terminal / zsh):**
   ```bash
   cd "<PROJECT_DIR>"
   pip3 install -r requirements.txt
   ```

3. **Optional – automatic login**  
   Copy `.env.example` to `.env` and set:

   ```env
   DOORDASH_EMAIL=your@email.com
   DOORDASH_PASSWORD=yourpassword
   ```

   If these are blank, the script can prompt in the terminal or wait while you sign in in the browser (including 2FA).

## Chrome debug mode (recommended)

With **`REMOTE_DEBUG = True`** in `config.py` (default), Selenium **attaches** to a Chrome you start yourself so your DoorDash session and profile persist.

1. **Close other Chrome windows** that might be using the same profile (or use the dedicated folder below only for this).

2. **Start Chrome** with remote debugging and a **separate user data directory**:

   **Windows (Command Prompt):**
   ```bat
   "<CHROME_PATH>" --remote-debugging-port=9222 --user-data-dir="<CHROME_DEBUG_DIR>"
   ```
   *(If Chrome is under `Program Files (x86)`, use that path instead.)*

   **Windows (PowerShell):**
   ```powershell
   & "<CHROME_PATH>" --remote-debugging-port=9222 --user-data-dir="<CHROME_DEBUG_DIR>"
   ```

   **macOS (Terminal / zsh):**
   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$HOME/ChromeDebug"
   ```

   This matches **`REMOTE_DEBUG_ADDRESS = "127.0.0.1:9222"`** in `config.py`. Log into DoorDash in **this** Chrome window as needed.

3. **Run the scraper** (next section) in another terminal while that Chrome stays open.

To let Selenium launch its own Chrome instead, set **`REMOTE_DEBUG = False`** in `config.py` (uses `BROWSER_PROFILE_DIR` for a dedicated profile).

> **Note on Session Persistence (`.browser-profile/`):**  
> When `REMOTE_DEBUG = False`, the script automatically initializes a local `.browser-profile/` directory to store session cookies and logins between runs so you don't need to re-login every time. This folder is ignored by git and does not require any manual setup or backfilling.

## Run

Run the project as a **module** from the **parent** of the `DoorDashReceipts` folder (so Python resolves the package name `DoorDashReceipts`):

**Windows (PowerShell / Command Prompt):**
```powershell
cd "<PARENT_DIR>"
py -m DoorDashReceipts.scraper
```

**macOS / Linux (Terminal / zsh):**
```bash
cd "<PARENT_DIR>"
python3 -m DoorDashReceipts.scraper
```

- If **`REMOTE_DEBUG`** is on: start Chrome with debug mode **before** this command.
- The script opens or uses the browser, goes to order history, applies the date range from `config.py`, scrolls the list as needed, then for each order opens the receipt, captures screenshot + PDF, and returns to the orders page.

**Output**

- **CSV:** `doordash_orders.csv` in the current working directory (usually the folder you ran from), unless you change `OUTPUT_CSV` in `config.py`.
- **Receipts:** `DOWNLOADS_DIR / doordash_receipts` (default: your **Downloads** folder + `doordash_receipts`), with `RECEIPTS_SUBDIR` and `DOWNLOADS_DIR` configurable in `config.py` or env.

## Configuration

Edit **`config.py`** (and optionally env vars) for:

| Area | What to change |
|------|----------------|
| **Dates** | `START_DATE`, `END_DATE` |
| **Meals** | `BREAKFAST_END_HOUR`, `LUNCH_END_HOUR` |
| **Output paths** | `DOWNLOADS_DIR`, `RECEIPTS_SUBDIR`, `OUTPUT_CSV` (or `DOWNLOADS_DIR` env) |
| **Remote debug** | `REMOTE_DEBUG`, `REMOTE_DEBUG_ADDRESS` |
| **Own Chrome profile** (no remote attach) | `REMOTE_DEBUG = False`, `BROWSER_PROFILE_DIR` |
| **Order list scrolling** | `ORDERS_SCROLL_MAX_ROUNDS`, `ORDERS_SCROLL_PAUSE_SECONDS`, `ORDERS_SCROLL_CONTAINER_SELECTOR` |
| **Receipt load wait** | `RECEIPT_PAGE_SETTLE_SECONDS` |
| **Receipt PDF layout** | `RECEIPT_PDF_HIDE_SELECTORS`, `RECEIPT_PDF_EXTRA_CSS` (map vs receipt pane) |
| **Viewport** | `VIEWPORT_WIDTH`, `VIEWPORT_HEIGHT` |

Selectors for DoorDash’s DOM live in `config.py`; if the site changes, update them there.

## Notes

- **Selectors** break when DoorDash updates the site; adjust `config.py` as needed.
- **Captcha / 2FA:** run headed (visible Chrome) and complete steps manually when prompted.
- **PDF:** generated with CDP `Page.printToPDF` (print media + optional CSS), not by driving the system print dialog.
