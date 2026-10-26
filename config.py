"""Configuration for DoorDash receipt scraper."""
import os
from pathlib import Path
from datetime import datetime

# Date range for orders: start date (Feb 5) through today
START_DATE = datetime(2026, 2, 5).date()
END_DATE = datetime.now().date()

# Meal classification by order time (hour, minute ignored for boundary)
BREAKFAST_END_HOUR = 11   # before 11:00 -> breakfast
LUNCH_END_HOUR = 16      # 11:00–14:59 -> lunch; 16:00+ -> dinner

# Output: CSV of orders and receipt files
DOWNLOADS_DIR = Path(
    os.environ.get(
        "DOWNLOADS_DIR",
        os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
    )
).resolve()
RECEIPTS_SUBDIR = "doordash_receipts"
OUTPUT_CSV = "doordash_orders.csv"

# Browser: run headed so you can complete 2FA or captcha if needed
HEADED = True

# DoorDash URLs
DOORDASH_BASE = "https://www.doordash.com"
ORDERS_URL = "https://www.doordash.com/orders/"

# Selectors (centralized here for easier maintenance)
ORDER_ITEM_SELECTOR = 'div[data-anchor-id="OrderHistoryOrderItem"]'
# DoorDash loads ~10 orders at a time; we scroll until the card count stops growing.
try:
    ORDERS_SCROLL_MAX_ROUNDS = max(5, int(os.environ.get("ORDERS_SCROLL_MAX_ROUNDS", "50")))
except ValueError:
    ORDERS_SCROLL_MAX_ROUNDS = 50
try:
    ORDERS_SCROLL_PAUSE_SECONDS = max(0.2, float(os.environ.get("ORDERS_SCROLL_PAUSE_SECONDS", "1.25")))
except ValueError:
    ORDERS_SCROLL_PAUSE_SECONDS = 2.25
try:
    ORDERS_SCROLL_STABLE_ROUNDS = max(1, int(os.environ.get("ORDERS_SCROLL_STABLE_ROUNDS", "3")))
except ValueError:
    ORDERS_SCROLL_STABLE_ROUNDS = 5
try:
    ORDERS_BOOTSTRAP_MAX_ROUNDS = max(
        1, int(os.environ.get("ORDERS_BOOTSTRAP_MAX_ROUNDS", "5"))
    )
except ValueError:
    ORDERS_BOOTSTRAP_MAX_ROUNDS = 5
# If the list scrolls inside a div (not the window), set a CSS selector here and we scroll that node too.
ORDERS_SCROLL_CONTAINER_SELECTOR: str = os.environ.get("ORDERS_SCROLL_CONTAINER_SELECTOR", "").strip()

DATE_HEADER_SELECTOR = 'div.sc-1c4183e3-23.cDuMiR'
RECEIPT_ORDER_TIME_SELECTOR = "div.sc-db1e4f53-1.dPiwYn"
DOWNLOAD_RECEIPT_BUTTON_SELECTOR = '[data-testid="downloadReceiptButton"]'

# Receipt PDF (CDP printToPDF): the orders receipt view is often split (map left, receipt right).
# Chrome print preview usually hides the map via @media print — we emulate "print" media first.
# If the PDF still includes the map, inspect the page (DevTools) and add selectors/CSS below.
RECEIPT_PDF_HIDE_SELECTORS: tuple[str, ...] = ()
# Example: ("div[data-anchor-id='ReceiptMapColumn']",) — replace with real selectors from your DOM.
RECEIPT_PDF_EXTRA_CSS: str = ""
# Example: ".some-map-wrapper { display: none !important; }"

# After receipt markers are detected, wait this many seconds so the pane can finish loading
# before order time, screenshot, and PDF. Helps avoid missing files when the UI is slow.
# Override: set env RECEIPT_PAGE_SETTLE_SECONDS (e.g. "8").
try:
    RECEIPT_PAGE_SETTLE_SECONDS = max(0, int(os.environ.get("RECEIPT_PAGE_SETTLE_SECONDS", "5")))
except ValueError:
    RECEIPT_PAGE_SETTLE_SECONDS = 10

# Screenshot sizing: Selenium's `save_screenshot()` captures the current viewport.
# Limit viewport height so screenshots aren't taller than the screen.
VIEWPORT_WIDTH = int(os.environ.get("VIEWPORT_WIDTH", "1280"))
VIEWPORT_HEIGHT = int(os.environ.get("VIEWPORT_HEIGHT", "900"))

# Attach to an already-open Chrome (remote debugging).
# Set to False to let Selenium launch its own Chrome.
REMOTE_DEBUG = True
REMOTE_DEBUG_ADDRESS = "127.0.0.1:9222"

# Reuse a dedicated Chrome profile so DoorDash sessions persist between runs.
BROWSER_PROFILE_DIR = Path(
    os.environ.get(
        "BROWSER_PROFILE_DIR",
        Path(__file__).resolve().parent / ".browser-profile",
    )
).resolve()
