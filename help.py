#!/usr/bin/env python3
"""Log in to the Argaam developer portal, create an app and register APIs.

Steps:
  1. Log in at https://developer.argaam.com/login
  2. Go to https://developer.argaam.com/apps
  3. New Application -> name -> Create Application
  4. Read the API key shown in the dialog, print it, save it, "Copy and close"
  5. On the app page: "Register an API" -> search each API -> register it

Credentials come from .env (never hard-code them here):

    ARGAAM_EMAIL=you@example.com
    ARGAAM_PASSWORD=...

Usage:

    python help.py                       # random app name, headless
    python help.py --app-name LexBiz2
    python help.py --headed              # watch the browser
"""

import argparse
import json
import os
import random
import re
import string
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = "https://developer.argaam.com"
LOGIN_URL = f"{BASE_URL}/login"
APPS_URL = f"{BASE_URL}/apps"

# Options in the dropdown: "Sandbox API Key" or "Production API Key".
AUTH_STRATEGY = "Sandbox API Key"

KEYS_FILE = Path(__file__).parent / "api_keys.json"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# Searched by name; only the result that also says "Sandbox" is registered.
APIS_TO_REGISTER = [
    "Fundamentals Layer API",
    "Investment Funds Layer API",
    "Macro Economics",
    "Regulatory Filings",
    "Market Data Layer API",
    "Projects & Capital Investments Layer API",
    "M&A & Transactions Layer API",
]

# Kong portal keys look like g6AJfvpkc9IZFLXtqZweoNzO3K0v6dFh (32 alnum chars).
KEY_PATTERN = re.compile(r"\b[A-Za-z0-9]{32}\b")


def log(msg: str) -> None:
    print(msg, flush=True)


def screenshot(page, name: str) -> None:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    path = SCREENSHOT_DIR / f"{datetime.now():%H%M%S}_{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log(f"    [screenshot] {path}")
    except Exception:
        pass


def click_button(scope, text: str, timeout: int = 20_000) -> None:
    """Click a button/link by its visible text (case-insensitive)."""
    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
    for role in ("button", "link"):
        loc = scope.get_by_role(role, name=pattern)
        try:
            loc.first.wait_for(state="visible", timeout=timeout // 2)
            loc.first.click()
            return
        except PlaywrightTimeoutError:
            continue
    # Last resort: any element containing the text.
    scope.get_by_text(pattern).first.click(timeout=timeout)


def login(page, email: str, password: str) -> None:
    log(f"[*] Opening {LOGIN_URL}")
    # Wait for the Nuxt app to hydrate; typing before that gets wiped and the
    # button does a plain form reload instead of the real login request.
    page.goto(LOGIN_URL, wait_until="networkidle", timeout=60_000)
    email_input = page.locator("#email")
    pw_input = page.locator("#password")
    email_input.wait_for(state="visible", timeout=30_000)
    page.wait_for_timeout(1_500)

    for attempt in range(1, 4):
        email_input.click()
        email_input.fill("")
        email_input.press_sequentially(email, delay=40)
        pw_input.click()
        pw_input.fill("")
        pw_input.press_sequentially(password, delay=40)
        if email_input.input_value() == email and pw_input.input_value() == password:
            break
        log(f"    [!] Fields not filled correctly, retrying ({attempt}/3)")
        page.wait_for_timeout(1_500)

    log("[*] Clicking 'Log in'")
    page.locator("[data-testid='submit-button']").click()

    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)
    except PlaywrightTimeoutError:
        screenshot(page, "login_failed")
        alert = page.locator("[role='alert'], .k-alert, .portal-k-alert")
        message = alert.first.inner_text().strip() if alert.count() else ""
        raise RuntimeError(
            "Login did not complete (still on /login)."
            + (f" Site says: {message}" if message else "")
        )
    log(f"[+] Logged in -> {page.url}")


def create_app(page, email: str, app_name: str, auth_strategy: str) -> str:
    log(f"[*] Opening {APPS_URL}")
    page.goto(APPS_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    log("[*] Clicking 'New Application'")
    click_button(page, "New Application")

    # The form is a slide-out panel ("Create an Application"), not a dialog.
    name_input = page.get_by_placeholder(re.compile(r"name for your application", re.I))
    name_input.wait_for(state="visible", timeout=15_000)
    name_input.fill(app_name)
    log(f"[*] Application name: {app_name}")

    select_auth_strategy(page, auth_strategy)

    log("[*] Clicking 'Create application'")
    create_btn = page.get_by_role("button", name=re.compile(r"^\s*create application\s*$", re.I))
    create_btn.last.click(timeout=15_000)

    # Panel "Application created successfully" shows the credential once.
    page.get_by_text(re.compile(r"application created successfully", re.I)).first.wait_for(
        state="visible", timeout=30_000
    )
    log("[+] Application created")
    page.wait_for_timeout(1_000)

    # Read and save the key BEFORE clicking anything, so it is never lost.
    api_key = extract_key(page)
    if api_key:
        print(api_key, flush=True)
        save_key(email, app_name, api_key)

    # "Copy and close" is not exposed as a role=button, so match by text.
    copy_btn = page.get_by_text(re.compile(r"^\s*copy\s*(and|&)\s*close\s*$", re.I))
    copy_btn.first.click(timeout=15_000)
    log("[+] Clicked 'Copy and close'")

    if not api_key:
        api_key = read_clipboard(page)
        if not api_key:
            screenshot(page, "no_key")
            raise RuntimeError("Could not read the API key from the panel or clipboard.")
        print(api_key, flush=True)
        save_key(email, app_name, api_key)

    open_app_page(page, app_name)
    return api_key


def open_app_page(page, app_name: str) -> None:
    """After closing the panel, make sure we're on the new app's page."""
    page.wait_for_timeout(2_000)
    register_btn = page.get_by_text(re.compile(r"^\s*register an api\s*$", re.I))
    if register_btn.count() and register_btn.first.is_visible():
        return
    log(f"[*] Opening app page for {app_name}")
    search = page.get_by_placeholder(re.compile(r"applications by name", re.I))
    if search.count():
        search.first.fill(app_name)
        page.wait_for_timeout(1_500)
    page.get_by_text(re.compile(rf"^\s*{re.escape(app_name)}\s*$")).first.click(timeout=15_000)
    register_btn.first.wait_for(state="visible", timeout=30_000)


def select_auth_strategy(page, wanted: str) -> None:
    """Open the 'Select an auth strategy' dropdown and pick `wanted`."""
    log(f"[*] Auth strategy: {wanted}")
    trigger = page.get_by_placeholder(re.compile(r"auth strategy", re.I))
    if trigger.count() == 0:
        trigger = page.get_by_text(re.compile(r"select an auth strategy", re.I))
    trigger.first.click(timeout=15_000)
    page.wait_for_timeout(500)

    pattern = re.compile(re.escape(wanted), re.I)
    option = page.get_by_role("option", name=pattern)
    if option.count() == 0:
        option = page.locator("li:visible, button:visible").filter(has_text=pattern)
    if option.count() == 0:
        option = page.get_by_text(pattern).filter(visible=True)
    if option.count() == 0:
        screenshot(page, "auth_strategy_options")
        raise RuntimeError(f"Auth strategy '{wanted}' not found in the dropdown.")
    option.first.click()


def visible_panel_input(page):
    """The search box inside whatever panel/modal was just opened."""
    inputs = page.locator("input:visible")
    for i in range(inputs.count() - 1, -1, -1):
        inp = inputs.nth(i)
        placeholder = (inp.get_attribute("placeholder") or "").lower()
        if "applications by name" in placeholder:
            continue  # the apps list search box behind the panel
        return inp
    return None


def extract_key(page) -> str:
    """Read the key text from the open credential panel."""
    candidates = []
    try:
        for inp in page.locator("input:visible, textarea:visible").all():
            candidates.append(inp.input_value())
        candidates.append(page.locator("body").inner_text())
    except Exception:
        pass
    for text in candidates:
        m = KEY_PATTERN.search(text or "")
        if m:
            return m.group(0)
    return ""


def read_clipboard(page) -> str:
    try:
        text = page.evaluate("navigator.clipboard.readText()")
    except Exception:
        return ""
    m = KEY_PATTERN.search(text or "")
    return m.group(0) if m else ""


def save_key(email: str, app_name: str, api_key: str) -> None:
    data = []
    if KEYS_FILE.exists():
        try:
            data = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = []
    data.append({
        "email": email,
        "app": app_name,
        "api_key": api_key,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    KEYS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log(f"[+] Saved key to {KEYS_FILE}")


def find_sandbox_option(page, search, api_name: str):
    """Return the search result whose text has the API name AND 'Sandbox'."""
    pattern = re.compile(rf"{re.escape(api_name)}.*sandbox|sandbox.*{re.escape(api_name)}", re.I | re.S)

    options = page.get_by_role("option").filter(has_text=pattern).filter(visible=True)
    if options.count():
        return options.first

    # Fallback: text matches below the search box (skips already-registered
    # APIs listed elsewhere on the app page), closest first.
    box = search.bounding_box() or {"y": 0, "height": 0}
    top = box["y"] + box["height"]
    best, best_dist = None, None
    matches = page.get_by_text(pattern).filter(visible=True)
    for i in range(matches.count()):
        el = matches.nth(i)
        el_box = el.bounding_box()
        if not el_box or el_box["y"] < top:
            continue
        dist = el_box["y"] - top
        if best_dist is None or dist < best_dist:
            best, best_dist = el, dist
    return best


def register_api(page, api_name: str) -> bool:
    log(f"[*] Registering: {api_name}")
    click_button(page, "Register an API")
    page.wait_for_timeout(1_500)

    search = visible_panel_input(page)
    if search is None:
        screenshot(page, "register_no_search")
        page.keyboard.press("Escape")
        return False
    search.click()
    search.fill(api_name)
    page.wait_for_timeout(1_500)

    option = find_sandbox_option(page, search, api_name)
    if option is None:
        log(f"[!] No Sandbox result for '{api_name}'")
        screenshot(page, "api_not_found")
        page.keyboard.press("Escape")
        return False
    log(f"    -> picking: {option.inner_text().strip().splitlines()[0]}")
    option.click()
    page.wait_for_timeout(500)

    # Confirm: the last visible "Register…" button (not "Register an API").
    register_re = re.compile(r"^\s*register(?!\s+an\s+api)", re.I)
    submit = page.get_by_role("button", name=register_re).filter(visible=True)
    if submit.count() == 0:
        submit = page.get_by_text(register_re).filter(visible=True)
    try:
        submit.last.click(timeout=15_000)
        search.wait_for(state="hidden", timeout=15_000)
    except PlaywrightTimeoutError:
        screenshot(page, "register_dialog_stuck")
        page.keyboard.press("Escape")
        return False
    log(f"[+] Registered: {api_name}")
    page.wait_for_timeout(1_000)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--app-name",
        default=None,
        help="app name (default: random, e.g. LexBiz-k3f9q2, since names must be unique)",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument(
        "--auth",
        default=AUTH_STRATEGY,
        choices=["Sandbox API Key", "Production API Key"],
        help="auth strategy for the new app",
    )
    args = parser.parse_args()
    if not args.app_name:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        args.app_name = f"LexBiz-{suffix}"

    load_dotenv()
    email = os.getenv("ARGAAM_EMAIL", "").strip()
    password = os.getenv("ARGAAM_PASSWORD", "").strip()
    if not email or not password:
        sys.exit("[x] Set ARGAAM_EMAIL and ARGAAM_PASSWORD in .env")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE_URL)
        page = context.new_page()
        try:
            login(page, email, password)
            api_key = create_app(page, email, args.app_name, args.auth)

            # After "Copy and close" the app page should be open.
            page.wait_for_load_state("networkidle", timeout=30_000)
            log(f"[*] App page: {page.url}")

            failed = [name for name in APIS_TO_REGISTER if not register_api(page, name)]
            ok = len(APIS_TO_REGISTER) - len(failed)
            log(f"\n[+] Registered {ok}/{len(APIS_TO_REGISTER)} APIs")
            if failed:
                log("[!] Failed: " + ", ".join(failed))
            log(f"\nAPI key: {api_key}")
        except Exception:
            screenshot(page, "error")
            raise
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except (PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[x] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
