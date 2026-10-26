"""
DoorDash order history scraper.
Logs in, collects orders in a date range, builds a table, and saves receipt screenshots/downloads.
Uses Selenium (no greenlet) so it works on Python 3.14.

Receipt PDFs: DoorDash's "Download receipt" opens Chrome's print UI; native print/save dialogs
cannot be driven reliably from Selenium. We use Chrome DevTools Page.printToPDF instead (same
output as choosing Save as PDF) and write the file to the receipts folder with the agreed name.
"""
import base64
import os
import re
import csv
import time as time_module
from pathlib import Path
from datetime import datetime, time

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from . import config

load_dotenv()

def _meal_from_time(order_time: time) -> str:
    """Classify meal from order time."""
    if order_time.hour < config.BREAKFAST_END_HOUR:
        return "breakfast"
    if order_time.hour < config.LUNCH_END_HOUR:
        return "lunch"
    return "dinner"


def _safe_filename(date_str: str, total: str) -> str:
    """Build filename [date]_[total-amount] safe for filesystem."""
    clean_total = re.sub(r"[^\d.]", "_", total).strip() or "0"
    if "." not in clean_total:
        clean_total += ".00"
    return f"{date_str}_{clean_total}"


_PDF_HELPER_STYLE_ID = "dd-scraper-receipt-pdf-style"


def _apply_receipt_pdf_layout(driver: webdriver.Chrome) -> None:
    """
    Make CDP printToPDF match Chrome's print preview: use print media, optional hide map pane.

    DoorDash often shows map + receipt on screen but only the receipt when printing; that is
    usually driven by @media print. Emulating media=print activates those rules. If anything
    is still wrong, set RECEIPT_PDF_HIDE_SELECTORS / RECEIPT_PDF_EXTRA_CSS in config.
    """
    extra_css = getattr(config, "RECEIPT_PDF_EXTRA_CSS", "") or ""
    selectors = tuple(getattr(config, "RECEIPT_PDF_HIDE_SELECTORS", ()) or ())

    driver.execute_script(
        """
        const styleId = arguments[0];
        const css = arguments[1];
        let st = document.getElementById(styleId);
        if (st) st.remove();
        st = document.createElement('style');
        st.id = styleId;
        st.textContent = css;
        document.head.appendChild(st);
        """,
        _PDF_HELPER_STYLE_ID,
        extra_css,
    )

    driver.execute_script(
        """
        const selectors = arguments[0];
        window.__ddScraperPdfRestore = [];
        for (const sel of selectors) {
          try {
            document.querySelectorAll(sel).forEach((node) => {
              const prev = node.getAttribute('style');
              window.__ddScraperPdfRestore.push([node, prev]);
              node.style.setProperty('display', 'none', 'important');
            });
          } catch (e) { /* ignore invalid selector */ }
        }
        """,
        list(selectors),
    )

    if extra_css or selectors:
        _log(
            f"PDF prep: print media + extra CSS ({len(extra_css)} chars) + "
            f"{len(selectors)} hide selector(s)."
        )
    else:
        _log(
            "PDF prep: print media emulation only (if the map appears in the PDF, set "
            "RECEIPT_PDF_HIDE_SELECTORS / RECEIPT_PDF_EXTRA_CSS in config.py)."
        )


def _restore_receipt_pdf_layout(driver: webdriver.Chrome) -> None:
    """Undo _apply_receipt_pdf_layout (DOM + styles)."""
    driver.execute_script(
        """
        const styleId = arguments[0];
        const st = document.getElementById(styleId);
        if (st) st.remove();
        const pairs = window.__ddScraperPdfRestore || [];
        for (const [node, prev] of pairs) {
          try {
            if (prev == null) node.removeAttribute('style');
            else node.setAttribute('style', prev);
          } catch (e) { /* stale node */ }
        }
        window.__ddScraperPdfRestore = [];
        """,
        _PDF_HELPER_STYLE_ID,
    )


def _save_receipt_pdf_via_cdp(driver: webdriver.Chrome, pdf_path: Path) -> bool:
    """
    Save the current page as PDF using Chrome DevTools (same pipeline as Print → Save as PDF).

    Uses print-media emulation so @media print rules (receipt-only layout) apply like the
    print preview. Avoids the native print dialog, which Selenium cannot control reliably.
    """
    try:
        driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "print"})
        _apply_receipt_pdf_layout(driver)
        time_module.sleep(0.35)

        result = driver.execute_cdp_cmd(
            "Page.printToPDF",
            {
                "printBackground": True,
                "preferCSSPageSize": True,
            },
        )
        raw = result.get("data")
        if not raw:
            _log("CDP Page.printToPDF returned no data.")
            return False
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(base64.b64decode(raw))
        return True
    except Exception as e:
        _log(f"Saving receipt PDF via Chrome CDP failed: {e}")
        return False
    finally:
        try:
            _restore_receipt_pdf_layout(driver)
        except Exception as e:
            _log(f"PDF layout restore failed (non-fatal): {e}")
        try:
            driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "screen"})
        except Exception:
            pass


def _log(msg: str) -> None:
    """Trace log for run progress."""
    print(f"[trace] {msg}")


def _get_credentials() -> tuple[str, str]:
    """Get email and password from env, prompting for them when not set."""
    _log("Checking credentials (env / .env / prompt)...")
    email = os.environ.get("DOORDASH_EMAIL", "").strip()
    password = os.environ.get("DOORDASH_PASSWORD", "").strip()
    if not email:
        _log("Email not in env → prompting for email.")
        print("Enter your DoorDash credentials here in the terminal (not in the browser window).")
        email = input("DoorDash email: ").strip()
    if not password:
        _log("Password not in env → prompting for password.")
        password = input("DoorDash password: ").strip()
    _log("Credentials ready.")
    return email, password


def _session_is_active(driver: webdriver.Chrome) -> bool:
    """Check whether DoorDash already considers this browser session signed in."""
    _log("Checking for an existing DoorDash session...")
    driver.get(config.ORDERS_URL)
    time_module.sleep(4)
    current_url = driver.current_url.lower()
    if "identity.doordash.com" in current_url or "login" in current_url or "signin" in current_url:
        _log("No active DoorDash session detected.")
        return False
    if "doordash.com/orders" in current_url or "doordash.com" in current_url:
        _log("Active DoorDash session detected; reusing it.")
        return True
    _log(f"Session check inconclusive (current URL: {driver.current_url}).")
    return False


def _login(driver: webdriver.Chrome) -> None:
    """Navigate to DoorDash and log in (or wait for manual login)."""
    if _session_is_active(driver):
        return

    _log("Opening DoorDash homepage...")
    driver.get(config.DOORDASH_BASE)
    time_module.sleep(3)

    email, password = _get_credentials()

    try:
        sign_in = driver.find_elements(By.CSS_SELECTOR, 'a[href*="login"], a[href*="signin"]')
        if not sign_in:
            sign_in = driver.find_elements(By.LINK_TEXT, "Sign in")
        if not sign_in:
            sign_in = driver.find_elements(By.PARTIAL_LINK_TEXT, "Sign in")
        if not sign_in:
            sign_in = driver.find_elements(By.PARTIAL_LINK_TEXT, "Log in")
        if sign_in:
            _log("Clicking Sign in...")
            sign_in[0].click()
            time_module.sleep(1.5)
    except Exception:
        pass

    if email and password:
        _log("Filling login form and submitting...")
        try:
            wait = WebDriverWait(driver, 10)
            email_input = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'input[type="email"], input[name="email"]')
                )
            )
            email_input.send_keys(email)
            password_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            password_input.send_keys(password)
            submit = driver.find_elements(By.CSS_SELECTOR, 'button[type="submit"]')
            if not submit:
                submit = driver.find_elements(
                    By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Log in')]"
                )
            if submit:
                submit[0].click()
                time_module.sleep(3)
        except Exception as e:
            _log(f"Form fill failed: {e}. Please log in manually in the browser.")
            print("Please log in in the browser, then press Enter here.")
            input()
    else:
        _log("No email/password → waiting for you to log in manually in the browser.")
        print("Please log in in the browser, then press Enter here.")
        input()

    _log("Waiting for login to complete (doordash.com)...")
    WebDriverWait(driver, 60).until(lambda d: "doordash.com" in d.current_url)
    _log("Login done.")


def _parse_date_from_header_text(header_text: str) -> datetime.date | None:
    """
    Parse date from DoorDash's date header.

    Header examples you provided:
    - 'Wed, Mar 4'
    - 'Mar 2'
    """
    header_text = (header_text or "").strip()
    if not header_text:
        return None

    # Only keep the first line in case the element has extra whitespace/children.
    header_line = header_text.splitlines()[0].strip()

    # Optional weekday + optional comma, then month + day.
    m = re.search(
        r"^(?:[A-Za-z]{3},\s*)?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\b",
        header_line,
        re.I,
    )
    if not m:
        return None

    month_str = m.group(1).title()
    day = int(m.group(2))

    months = {
        "Jan": 1,
        "Feb": 2,
        "Mar": 3,
        "Apr": 4,
        "May": 5,
        "Jun": 6,
        "Jul": 7,
        "Aug": 8,
        "Sep": 9,
        "Oct": 10,
        "Nov": 11,
        "Dec": 12,
    }
    month = months.get(month_str)
    if not month:
        return None

    # Your range is within a single year; use START_DATE's year.
    year = config.START_DATE.year
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def _parse_order_details_from_card_text(text: str) -> dict | None:
    """Parse meal/vendor/total/time from an order card (without date)."""
    time_match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text, re.I)
    if time_match:
        h, m = int(time_match.group(1)), int(time_match.group(2))
        ampm = (time_match.group(3) or "").lower()
        if ampm == "pm" and h != 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
        order_time = time(h, m)
    else:
        order_time = time(12, 0)

    total_match = re.search(r"\$\s*([\d,]+\.?\d*)", text)
    total_str = total_match.group(1).replace(",", "") if total_match else "0.00"

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    vendor = "Unknown"
    for line in lines:
        if not re.match(r"^[\d$]", line) and "order" not in line.lower() and len(line) > 2:
            vendor = line[:80]
            break

    return {
        "meal": _meal_from_time(order_time),
        "vendor": vendor,
        "total_charge": total_str,
        "order_time": order_time,
    }


def _parse_time_12h(time_text: str) -> time | None:
    """Parse '2:33 PM' into a datetime.time."""
    time_text = (time_text or "").strip()
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", time_text, re.I)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = m.group(3).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    try:
        return time(hour, minute)
    except ValueError:
        return None


def _format_time_12h(order_time: time) -> str:
    """Format datetime.time as 'H:MM AM/PM'."""
    # datetime formatting for consistency across platforms.
    dummy = datetime(2000, 1, 1, order_time.hour, order_time.minute)
    s = dummy.strftime("%I:%M %p").lstrip("0")
    return s


def _order_identity_key(order: dict) -> tuple[str, str, str]:
    """Stable key to match an order dict across DOM re-parses."""
    return (
        str(order.get("date", "")),
        (order.get("vendor") or "").strip()[:120],
        str(order.get("total_charge", "")),
    )


def _parse_orders_in_range_from_dom(driver: webdriver.Chrome) -> list[dict]:
    """
    Parse all order cards currently in the DOM; keep only those in START_DATE..END_DATE.
    Returns ordered list of order dicts (each includes 'date' ISO string).
    """
    cards = driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR)
    result: list[dict] = []
    for i in range(len(cards)):
        try:
            text = cards[i].text
            order_details = _parse_order_details_from_card_text(text)
            if not order_details:
                continue
            header_text = driver.execute_script(
                """
                const card = arguments[0];
                const selector = arguments[1];
                const headers = Array.from(document.querySelectorAll(selector));
                let last = null;
                for (const h of headers) {
                    const pos = h.compareDocumentPosition(card);
                    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) {
                        last = h;
                    }
                }
                return last ? last.textContent.trim() : null;
                """,
                cards[i],
                config.DATE_HEADER_SELECTOR,
            )
            order_date = _parse_date_from_header_text(header_text)
            if not order_date:
                continue
            if not (config.START_DATE <= order_date <= config.END_DATE):
                continue
            result.append({**order_details, "date": order_date.isoformat()})
        except Exception as e:
            _log(f"Skip card {i} while parsing DOM: {e}")
    return result


def _merge_new_visible_orders(
    driver: webdriver.Chrome,
    all_orders: list[dict],
    seen_keys: set[tuple[str, str, str]],
) -> int:
    """Append in-range orders visible in the DOM but not yet in seen_keys. Returns how many added."""
    added = 0
    cards = driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR)
    for i in range(len(cards)):
        try:
            text = cards[i].text
            order_details = _parse_order_details_from_card_text(text)
            if not order_details:
                continue
            header_text = driver.execute_script(
                """
                const card = arguments[0];
                const selector = arguments[1];
                const headers = Array.from(document.querySelectorAll(selector));
                let last = null;
                for (const h of headers) {
                    const pos = h.compareDocumentPosition(card);
                    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) {
                        last = h;
                    }
                }
                return last ? last.textContent.trim() : null;
                """,
                cards[i],
                config.DATE_HEADER_SELECTOR,
            )
            order_date = _parse_date_from_header_text(header_text)
            if not order_date:
                continue
            if not (config.START_DATE <= order_date <= config.END_DATE):
                continue
            row = {**order_details, "date": order_date.isoformat()}
            key = _order_identity_key(row)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_orders.append(row)
            added += 1
        except Exception:
            continue
    return added


def _find_card_index_for_order(driver: webdriver.Chrome, order: dict) -> int:
    """Return DOM index of the order card matching this order, or -1."""
    want = _order_identity_key(order)
    cards = driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR)
    for i in range(len(cards)):
        try:
            text = cards[i].text
            order_details = _parse_order_details_from_card_text(text)
            if not order_details:
                continue
            header_text = driver.execute_script(
                """
                const card = arguments[0];
                const selector = arguments[1];
                const headers = Array.from(document.querySelectorAll(selector));
                let last = null;
                for (const h of headers) {
                    const pos = h.compareDocumentPosition(card);
                    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) {
                        last = h;
                    }
                }
                return last ? last.textContent.trim() : null;
                """,
                cards[i],
                config.DATE_HEADER_SELECTOR,
            )
            order_date = _parse_date_from_header_text(header_text)
            if not order_date:
                continue
            if not (config.START_DATE <= order_date <= config.END_DATE):
                continue
            row = {**order_details, "date": order_date.isoformat()}
            if _order_identity_key(row) == want:
                return i
        except Exception:
            continue
    return -1


def _order_scroll_single_round(driver: webdriver.Chrome) -> None:
    """One scroll pass to trigger the next batch of lazy-loaded order cards (~10)."""
    pause = float(getattr(config, "ORDERS_SCROLL_PAUSE_SECONDS", 1.25))
    container_sel = getattr(config, "ORDERS_SCROLL_CONTAINER_SELECTOR", "") or ""
    cards = driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR)
    try:
        driver.execute_script(
            "window.scrollTo(0, Math.max("
            "document.body.scrollHeight, document.documentElement.scrollHeight));"
        )
        if container_sel:
            driver.execute_script(
                """
                const el = document.querySelector(arguments[0]);
                if (el) { el.scrollTop = el.scrollHeight; }
                """,
                container_sel,
            )
        if cards:
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'end', inline: 'nearest'});",
                cards[-1],
            )
    except Exception as e:
        _log(f"Scroll round warning: {e}")
    time_module.sleep(pause)


def _scroll_order_list_until_stable(driver: webdriver.Chrome) -> int:
    """
    Bootstrap lazy-loaded order history by scrolling until card count stabilizes.

    Stops early when the number of visible cards has stayed unchanged for
    ORDERS_SCROLL_STABLE_ROUNDS rounds, or when ORDERS_BOOTSTRAP_MAX_ROUNDS is reached.
    Returns the final count of cards visible in the DOM.
    """
    max_rounds = max(1, int(getattr(config, "ORDERS_BOOTSTRAP_MAX_ROUNDS", 5)))
    stable_target = max(1, int(getattr(config, "ORDERS_SCROLL_STABLE_ROUNDS", 3)))
    stable_rounds = 0
    prev_count = len(driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR))

    _log(
        f"Bootstrapping order list via scrolling: up to {max_rounds} round(s), "
        f"stop after {stable_target} stable round(s). Start count={prev_count}."
    )
    for round_idx in range(1, max_rounds + 1):
        _order_scroll_single_round(driver)
        current_count = len(driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR))
        if current_count == prev_count:
            stable_rounds += 1
            _log(
                f"Bootstrap round {round_idx}: count still {current_count} "
                f"(stable {stable_rounds}/{stable_target})."
            )
        else:
            _log(
                f"Bootstrap round {round_idx}: card count {prev_count} -> {current_count}."
            )
            stable_rounds = 0
            prev_count = current_count
        if stable_rounds >= stable_target:
            _log(
                f"Bootstrap stopped early after {round_idx} round(s): "
                f"card count stabilized at {current_count}."
            )
            return current_count

    final_count = len(driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR))
    _log(f"Bootstrap reached max rounds ({max_rounds}); final card count={final_count}.")
    return final_count


def _wait_for_receipt_page(driver: webdriver.Chrome, timeout: int = 12) -> bool:
    """Wait until we're on a receipt route and receipt-specific elements are visible."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: (
                not _is_orders_list_url(d.current_url)
                and bool(re.search(r"/orders/[0-9a-f-]+", d.current_url.lower()))
                and (
                    any(
                        e.is_displayed()
                        for e in d.find_elements(
                            By.CSS_SELECTOR, config.DOWNLOAD_RECEIPT_BUTTON_SELECTOR
                        )
                    )
                    or any(
                        e.is_displayed()
                        for e in d.find_elements(
                            By.CSS_SELECTOR, config.RECEIPT_ORDER_TIME_SELECTOR
                        )
                    )
                )
            )
        )
        return True
    except Exception:
        return False


def _click_element_robust(driver: webdriver.Chrome, element, label: str) -> bool:
    """Try a normal click, then JS click if the element is not interactable."""
    try:
        try:
            tag = element.tag_name
            txt = (element.text or "").strip().replace("\n", " | ")[:120]
            cls = element.get_attribute("class")
            testid = element.get_attribute("data-testid")
            anchor = element.get_attribute("data-anchor-id")
            _log(
                f"{label}: target tag={tag} class={cls} data-testid={testid} "
                f"data-anchor-id={anchor} text='{txt}'"
            )
        except Exception as meta_e:
            _log(f"{label}: could not read target metadata ({meta_e}).")
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            element,
        )
        # Nudge viewport to avoid sticky overlays intercepting clicks near the top.
        driver.execute_script("window.scrollBy(0, -120);")
        time_module.sleep(0.2)
        element.click()
        return True
    except Exception as e:
        _log(f"{label}: normal click failed ({e}); trying JS click.")
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception as js_e:
            _log(f"{label}: JS click failed ({js_e}).")
            return False


def _is_orders_list_url(url: str) -> bool:
    """True when URL is the generic /orders list page (not a specific order route)."""
    return (url or "").lower().rstrip("/").endswith("/orders")


def _find_receipt_action_in_card(card):
    """Find a clickable receipt action element inside an order card."""
    view_receipt_elems = card.find_elements(
        By.XPATH,
        ".//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'view receipt')]"
        " | .//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'view receipt')]",
    )
    if not view_receipt_elems:
        view_receipt_elems = card.find_elements(
            By.XPATH,
            ".//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'receipt')]"
            " | .//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'receipt')]",
        )
        _log(f"View-receipt elements found in card (fallback): {len(view_receipt_elems)}")
    else:
        _log(f"View-receipt elements found in card (strict): {len(view_receipt_elems)}")
    if not view_receipt_elems:
        return None
    visible_receipt_elems = [e for e in view_receipt_elems if e.is_displayed() and e.is_enabled()]
    _log(
        f"View-receipt elements visible+enabled: {len(visible_receipt_elems)} / {len(view_receipt_elems)}"
    )
    return visible_receipt_elems[0] if visible_receipt_elems else view_receipt_elems[0]


def _get_orders_from_page(driver: webdriver.Chrome) -> list[dict]:
    """
    Navigate to order history, bootstrap lazy loading, then parse in-range cards from DOM.

    Bootstrap runs once here using scroll-until-stable (capped by
    ORDERS_BOOTSTRAP_MAX_ROUNDS). During run(), milestone scroll + merge remains as
    a safety net after each reload (/orders starts near the top again).
    """
    _log("Navigating to order history...")
    driver.get(config.ORDERS_URL)
    _log("Waiting ~10s for order list to fully render...")
    time_module.sleep(10)
    WebDriverWait(driver, 20).until(
        EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR)
        )
    )
    _scroll_order_list_until_stable(driver)
    result = _parse_orders_in_range_from_dom(driver)
    n_cards = len(driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR))
    _log(
        f"Initial load: {n_cards} order card(s) in DOM, {len(result)} in date range "
        f"({config.START_DATE} to {config.END_DATE}). More load as we scroll per order batch."
    )
    return result


def _open_receipt_and_capture(
    driver: webdriver.Chrome,
    order: dict,
    receipts_dir: Path,
    *,
    skip_open: bool = False,
) -> tuple[str | None, time | None]:
    """
    Screenshot receipt and save PDF as [date]_[total].pdf (via Chrome CDP print-to-PDF).

    Returns:
      - (order_time_text, order_time_obj) extracted from the receipt page, if available.

    If skip_open=True, we assume the receipt view is already open and we will not
    click any "View receipt"/"Receipt" button again.
    """
    date_str = order["date"]
    total_str = order["total_charge"]
    fname_base = _safe_filename(date_str, total_str)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    receipt_time_text: str | None = None
    receipt_time_obj: time | None = None

    

    if not skip_open:
        _log("Looking for Receipt / View receipt link...")
        receipt_links = driver.find_elements(By.PARTIAL_LINK_TEXT, "Receipt")
        _log(f"Receipt links found by partial text: {len(receipt_links)}")
        if not receipt_links:
            receipt_links = driver.find_elements(
                By.XPATH, "//a[contains(., 'View receipt') or contains(., 'receipt')]"
            )
            _log(f"Receipt links found by XPath fallback: {len(receipt_links)}")
        if receipt_links:
            clickable_links = [e for e in receipt_links if e.is_displayed() and e.is_enabled()]
            _log(f"Receipt links visible+enabled: {len(clickable_links)} / {len(receipt_links)}")
            target = clickable_links[0] if clickable_links else receipt_links[0]
            if not _click_element_robust(driver, target, "Receipt link"):
                _log("No interactable receipt link; screenshotting current page.")
            else:
                time_module.sleep(1.5)
                _log("Receipt view opened.")
        else:
            _log("No receipt button found; screenshotting current page.")
    else:
        _log("skip_open=True; assuming receipt view is already open.")

    # Ensure we're on an actual receipt page before screenshotting.
    _log(f"Current URL before receipt-page wait: {driver.current_url}")
    if _is_orders_list_url(driver.current_url):
        _log("Still on /orders list URL; not a receipt page. Skipping capture for this order.")
        return None, None
    if not _wait_for_receipt_page(driver, timeout=12):
        _log("Receipt page markers not found; skipping screenshot to avoid capturing list page.")
        return None, None

    settle_s = getattr(config, "RECEIPT_PAGE_SETTLE_SECONDS", 5)
    if settle_s > 0:
        _log(
            f"Waiting {settle_s}s for receipt page to settle before order time / screenshot / PDF..."
        )
        time_module.sleep(settle_s)

    # Extract the order time from the receipt page (e.g. '2:33 PM').
    _log("Looking for order time on receipt page...")
    try:
        order_time_elem = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, config.RECEIPT_ORDER_TIME_SELECTOR))
        )
        receipt_time_text = order_time_elem.text.strip().splitlines()[0].strip()
        receipt_time_obj = _parse_time_12h(receipt_time_text)
        if receipt_time_obj:
            _log(
                f"Receipt page timestamp captured: {receipt_time_text} "
                f"(parsed as {_format_time_12h(receipt_time_obj)} for meal classification)"
            )
        else:
            _log(f"Receipt page timestamp text read but could not parse: {receipt_time_text}")
    except Exception as e:
        _log(f"Order time not found on receipt page (continuing): {e}")

    screenshot_path = receipts_dir / f"{fname_base}.png"
    driver.save_screenshot(str(screenshot_path))
    _log(f"Screenshot saved: {screenshot_path}")

    # PDF: use Chrome DevTools Page.printToPDF (same result as Print → Save as PDF) so we
    # never rely on the print dialog / "destination" dropdown, which native UI cannot automate.
    pdf_path = receipts_dir / f"{fname_base}.pdf"
    _log(f"Writing receipt PDF (CDP printToPDF) → {pdf_path.name}")
    if _save_receipt_pdf_via_cdp(driver, pdf_path):
        _log(f"Receipt PDF saved: {pdf_path}")
    else:
        _log("Receipt PDF not saved; screenshot PNG is still available.")

    return receipt_time_text, receipt_time_obj


def run() -> None:
    """Main: one browser session — login, orders in range, for each: add to table, open order, screenshot + download receipt."""
    _log("========== DoorDash scraper starting ==========")
    _log(
        f"Date range: {config.START_DATE} to {config.END_DATE}. Receipts → {config.DOWNLOADS_DIR / config.RECEIPTS_SUBDIR}"
    )

    receipts_dir = config.DOWNLOADS_DIR / config.RECEIPTS_SUBDIR
    receipts_dir.mkdir(parents=True, exist_ok=True)
    table_path = Path(config.OUTPUT_CSV)

    _log("Setting up Chrome (Selenium + webdriver-manager)...")
    options = Options()
    

    if getattr(config, "REMOTE_DEBUG", False):
        # Attach to the Chrome you started with --remote-debugging-port
        _log(f"Attaching to existing Chrome at {config.REMOTE_DEBUG_ADDRESS}...")
        options.debugger_address = config.REMOTE_DEBUG_ADDRESS
    else:
        # Selenium launches its own Chrome with a dedicated profile
        options.add_argument(f"--window-size={config.VIEWPORT_WIDTH},{config.VIEWPORT_HEIGHT}")
        options.add_argument("--lang=en-US")
        if not config.HEADED:
            options.add_argument("--headless")
        options.add_argument(f"--user-data-dir={config.BROWSER_PROFILE_DIR}")
        config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    service = Service(ChromeDriverManager().install())

    #Initiate chrome with the driver that was defined with the options above
    driver = webdriver.Chrome(service=service, options=options)
    # Ensure screenshot viewport matches our desired size when Selenium owns the window.
    if not getattr(config, "REMOTE_DEBUG", False):
        try:
            driver.set_window_size(config.VIEWPORT_WIDTH, config.VIEWPORT_HEIGHT)
            time_module.sleep(0.5)
        except Exception as e:
            _log(f"Could not set window size (continuing): {e}")

    try:
        _login(driver)
    except Exception as e:
        _log(f"Login or order list failed: {e}")
        driver.quit()
        return

    try:
        order_entries = _get_orders_from_page(driver)
    except Exception as e:
        _log(f"Getting an order list failed: {e}")
        driver.quit()
        return

    if not order_entries:
        _log("No orders found in date range. Check selectors and that you're on the orders page.")
        driver.quit()
        return

    seen_keys: set[tuple[str, str, str]] = {_order_identity_key(o) for o in order_entries}
    rows: list[dict] = []
    main_window = driver.current_window_handle
    pos = 1
    max_scroll = getattr(config, "ORDERS_SCROLL_MAX_ROUNDS", 200)

    while True:
        if pos > len(order_entries):
            _log(f"Finished queue: processed through position {pos - 1}.")
            break

        order = order_entries[pos - 1]

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR))
            )
        except Exception as exc:
            _log(f"Order cards not found on /orders (pos={pos}): {exc}")
            break

        # After each receipt we reload /orders; re-apply scroll depth so batch N is in the DOM.
        need_rounds = min(pos // 10, max_scroll)
        if need_rounds:
            _log(
                f"Orders list: applying {need_rounds} scroll round(s) before order {pos} "
                f"(milestone pos//10, capped at {max_scroll})."
            )
            for _ in range(need_rounds):
                _order_scroll_single_round(driver)

        guard = 0
        while pos > len(order_entries):
            added = _merge_new_visible_orders(driver, order_entries, seen_keys)
            if added:
                _log(f"Merged {added} new order row(s) from DOM; queue length {len(order_entries)}.")
                break
            _order_scroll_single_round(driver)
            guard += 1
            if guard > max_scroll:
                _log(
                    f"Stopped loading more orders after {max_scroll} extra scroll(s) "
                    f"(still need position {pos}, have {len(order_entries)})."
                )
                break
            added = _merge_new_visible_orders(driver, order_entries, seen_keys)
            if added:
                _log(f"Merged {added} new order row(s) after scroll; queue length {len(order_entries)}.")
                break

        if pos > len(order_entries):
            _log(
                f"No order at position {pos} (queue has {len(order_entries)}); "
                "assuming end of in-range history."
            )
            break

        # Fallback order time from the order card text (may be inaccurate).
        fallback_time_text = ""
        if isinstance(order.get("order_time"), time):
            fallback_time_text = _format_time_12h(order["order_time"])

        _log(
            f"--- Order {pos} (queue {len(order_entries)}): {order['date']} | "
            f"{order['vendor']} | ${order['total_charge']} | {order['meal']} ---"
        )
        row_idx = len(rows)
        rows.append(
            {
                "date": order["date"],
                "meal": order["meal"],
                "vendor": order["vendor"],
                "total_charge": order["total_charge"],
                "order_time": fallback_time_text,
            }
        )

        try:
            _log("Clicking 'View receipt' inside order card...")
            cards = driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR)
            _log(f"Order cards currently in DOM: {len(cards)}")

            idx = _find_card_index_for_order(driver, order)
            if idx < 0:
                raise RuntimeError(
                    f"Could not find card for date={order['date']} vendor={order['vendor']} "
                    f"total={order['total_charge']} (try ORDERS_SCROLL_CONTAINER_SELECTOR or "
                    "increase ORDERS_SCROLL_MAX_ROUNDS)."
                )

            card = cards[idx]
            _log(
                f"Using card idx={idx} for order date={order['date']} "
                f"vendor={order['vendor']} total={order['total_charge']}"
            )

            target_elem = _find_receipt_action_in_card(card)
            if target_elem:
                opened_receipt = False
                for attempt in (1, 2):
                    handles_before = set(driver.window_handles)
                    _log(f"Window handles before click: {len(handles_before)}")
                    if not _click_element_robust(driver, target_elem, f"View receipt (attempt {attempt})"):
                        if attempt == 2:
                            break
                    else:
                        time_module.sleep(1.0)
                        handles_after = set(driver.window_handles)
                        _log(f"Window handles after click: {len(handles_after)}")
                        new_handles = list(handles_after - handles_before)
                        if new_handles:
                            driver.switch_to.window(new_handles[-1])
                            _log("Switched to new receipt tab/window.")
                        if _wait_for_receipt_page(driver, timeout=10):
                            opened_receipt = True
                            break
                        _log(f"Receipt page not ready after click attempt {attempt}.")

                    if attempt == 2:
                        break
                    if driver.current_window_handle != main_window:
                        driver.close()
                        driver.switch_to.window(main_window)
                        _log("Closed non-ready receipt tab/window and switched back to orders tab.")
                    driver.get(config.ORDERS_URL)
                    time_module.sleep(1.0)
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR))
                    )
                    # Reapply milestone scroll depth after list reload.
                    rounds = min(pos // 10, max_scroll)
                    for _ in range(rounds):
                        _order_scroll_single_round(driver)
                    cards = driver.find_elements(By.CSS_SELECTOR, config.ORDER_ITEM_SELECTOR)
                    idx = _find_card_index_for_order(driver, order)
                    if idx < 0:
                        break
                    card = cards[idx]
                    target_elem = _find_receipt_action_in_card(card)
                    if not target_elem:
                        break

                if not opened_receipt:
                    raise RuntimeError("Could not open receipt page after retry.")

                receipt_time_text, receipt_time_obj = _open_receipt_and_capture(
                    driver, order, receipts_dir, skip_open=True
                )
            else:
                _log("No 'view receipt' element found in card; falling back to clicking card.")
                if not _click_element_robust(driver, card, "Order card fallback"):
                    raise RuntimeError("Could not click order card fallback.")
                time_module.sleep(1.5)

                receipt_time_text, receipt_time_obj = _open_receipt_and_capture(
                    driver, order, receipts_dir, skip_open=False
                )

            # Update CSV values based on receipt page time (preferred).
            if receipt_time_obj:
                order["meal"] = _meal_from_time(receipt_time_obj)
                rows[row_idx]["meal"] = order["meal"]
            if receipt_time_text:
                rows[row_idx]["order_time"] = receipt_time_text

            # If receipt opened in a separate tab/window, close it and return.
            if driver.current_window_handle != main_window:
                driver.close()
                driver.switch_to.window(main_window)
                _log("Closed receipt tab/window and switched back to orders tab.")
            _log("Back to order list...")
            driver.get(config.ORDERS_URL)
            time_module.sleep(1.5)
            pos += 1
        except Exception as e:
            _log(
                f"Receipt capture failed for order pos={pos}, date={order.get('date')}, "
                f"vendor={order.get('vendor')}, total={order.get('total_charge')}: {e}"
            )
            driver.get(config.ORDERS_URL)
            time_module.sleep(1)
            pos += 1

    driver.quit()

    _log("Writing orders table to CSV...")
    with open(table_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "meal", "vendor", "total_charge", "order_time"])
        w.writeheader()
        w.writerows(rows)
    _log(f"Table saved: {table_path.absolute()}")
    _log(f"Receipts saved: {receipts_dir.absolute()}")
    _log("========== Done ==========")


def main() -> None:
    run()


if __name__ == "__main__":
    main()

