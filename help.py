#!/usr/bin/env python3
"""Log in to the Argaam developer portal, create apps and register APIs.

Steps:
  1. Log in at https://developer.argaam.com/login
  2. Go to https://developer.argaam.com/apps
  3. New Application -> name -> Create Application
  4. Read the API key shown in the dialog, print it, save it, "Copy and close"
  5. On the app page: "Register an API" -> search each API -> register it
  6. Click "<- Applications" and repeat 3-5 until --count apps are made (default 10)

Credentials come from .env (never hard-code them here):

    ARGAAM_EMAIL=you@example.com
    ARGAAM_PASSWORD=...

Usage:

    python help.py                       # 10 apps, random names, headless
    python help.py --count 3             # only 3 apps
    python help.py --app-name LexBiz     # LexBiz1 ... LexBiz10
    python help.py --headed              # watch the browser
"""

import argparse
import json
import os
import random
import re
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

# Exact names, searched as-is: each search returns just the Sandbox API.
APIS_TO_REGISTER = [
    "Fundamentals Layer API (Sandbox)",
    "Investment Funds Layer API (Sandbox)",
    "Macroeconomic Layer API (Sandbox)",
    "Regulatory Filings Layer API (Sandbox)",
    "Market Data Layer API (Sandbox)",
    "Projects & Capital Investments Layer API (Sandbox)",
    "M&A & Transactions Layer API (Sandbox)",
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


def is_apps_list(url: str) -> bool:
    return url.rstrip("/").split("?")[0] == APPS_URL


def back_to_applications(page) -> None:
    """Click the '<- Applications' back link on the app page.
    Falls back to opening /apps directly if the link isn't found."""
    log("[*] Clicking '<- Applications'")
    try:
        link = page.get_by_role("link", name=re.compile(r"applications", re.I))
        link.first.click(timeout=10_000)
        page.wait_for_url(is_apps_list, timeout=15_000)
    except PlaywrightTimeoutError:
        log("    [!] Back link not found, opening /apps directly")
        page.goto(APPS_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=30_000)


def create_app(page, email: str, app_name: str, auth_strategy: str) -> str:
    if not is_apps_list(page.url):
        log(f"[*] Opening {APPS_URL}")
        page.goto(APPS_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    # The form is a slide-out panel ("Create an Application"), not a dialog.
    name_input = page.get_by_placeholder(re.compile(r"name for your application", re.I))

    # The button that opens the form is labelled differently depending on the
    # view: try "Create application" first, then "New Application".
    for label in ("Create application", "New Application"):
        log(f"[*] Trying '{label}' button")
        try:
            click_button(page, label, timeout=8_000)
            name_input.wait_for(state="visible", timeout=8_000)
            log(f"[+] Opened form with '{label}'")
            break
        except PlaywrightTimeoutError:
            log(f"    [!] '{label}' not found or form did not open")
    else:
        screenshot(page, "no_create_button")
        raise RuntimeError("Neither 'Create application' nor 'New Application' opened the form.")
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


def load_accounts() -> list:
    """Return api_keys.json as a list of accounts:
    [{"email": ..., "api_keys": [{"app", "api_key", "created_at"}, ...]}, ...]
    Old flat entries ({"email", "app", "api_key", "created_at"}) are folded in."""
    if not KEYS_FILE.exists():
        return []
    try:
        raw = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    accounts: list = []
    by_email: dict = {}
    for entry in raw if isinstance(raw, list) else []:
        email = entry.get("email")
        if not email:
            continue
        acc = by_email.get(email)
        if acc is None:
            acc = {"email": email, "api_keys": []}
            by_email[email] = acc
            accounts.append(acc)
        if "api_keys" in entry:
            acc["api_keys"].extend(entry["api_keys"])
        else:  # legacy flat entry
            acc["api_keys"].append({
                "app": entry.get("app"),
                "api_key": entry.get("api_key"),
                "created_at": entry.get("created_at"),
            })
    return accounts


def save_key(email: str, app_name: str, api_key: str) -> None:
    accounts = load_accounts()
    acc = next((a for a in accounts if a["email"] == email), None)
    if acc is None:
        acc = {"email": email, "api_keys": []}
        accounts.append(acc)
    acc["api_keys"].append({
        "app": app_name,
        "api_key": api_key,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    KEYS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    log(f"[+] Saved key to {KEYS_FILE}")


# The card button says "Register"; after registering it becomes "Unregister API".
REGISTER_BTN = re.compile(r"^\s*register(\s+api)?\s*$", re.I)
UNREGISTER_BTN = re.compile(r"^\s*unregister(\s+api)?\s*$", re.I)


def sandbox_pattern(api_name: str):
    # Full name; card titles wrap onto two lines, so allow any whitespace.
    words = r"\s+".join(re.escape(w) for w in api_name.split())
    return re.compile(words, re.I)


def open_register_panel(page):
    """Click 'Register an API' and return (panel, search input)."""
    log("[*] Opening 'Register an API' panel")
    click_button(page, "Register an API")
    header = re.compile(r"add apis to", re.I)
    page.get_by_text(header).first.wait_for(state="visible", timeout=15_000)
    # Deepest element holding both the "Add APIs to ..." title and an input.
    panel = page.locator("div").filter(has_text=header).filter(has=page.locator("input")).last
    search = panel.locator("input:visible").first
    search.wait_for(state="visible", timeout=15_000)
    return panel, search


def sandbox_card(panel, api_name: str):
    """The result card for exactly `api_name` (never a Production card)."""
    btn = panel.page.get_by_text(re.compile(r"^\s*(un)?register(\s+api)?\s*$", re.I))
    return (
        panel.locator("div")
        .filter(has_text=sandbox_pattern(api_name))
        .filter(has_not_text=re.compile(r"\(production\)", re.I))
        .filter(has=btn)
        .last
    )


def register_api(page, panel, search, api_name: str) -> bool:
    log(f"[*] Registering: {api_name}")

    # Clear the previous search, then type the next API name.
    search.click()
    search.fill("")
    page.wait_for_timeout(500)
    search.fill(api_name)
    page.wait_for_timeout(2_000)

    card = sandbox_card(panel, api_name)
    try:
        card.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeoutError:
        log(f"[!] No Sandbox result for '{api_name}'")
        screenshot(page, "api_not_found")
        return False

    if card.get_by_text(UNREGISTER_BTN).count():
        log(f"[=] Already registered: {api_name}")
        return True

    card.get_by_text(REGISTER_BTN).first.click(timeout=15_000)
    # Success = button turns into "Unregister API" (or "Register" disappears,
    # e.g. when the API goes to pending approval).
    for _ in range(40):
        page.wait_for_timeout(500)
        if card.get_by_text(UNREGISTER_BTN).count() or not card.get_by_text(REGISTER_BTN).count():
            break
    else:
        log(f"[!] '{api_name}' still shows 'Register' after clicking")
        screenshot(page, "register_failed")
        return False
    log(f"[+] Registered: {api_name}")
    return True


def register_all(page, names) -> list:
    panel, search = open_register_panel(page)
    failed = [name for name in names if not register_api(page, panel, search, name)]
    # Close the panel (X button / Escape) so the app page shows the list.
    page.keyboard.press("Escape")
    return failed


NAME_PREFIXES = [
    "Nova", "Astra", "Falcon", "Orbit", "Zenith", "Atlas", "Cobalt", "Vertex",
    "Quanta", "Summit", "Harbor", "Pioneer", "Lumen", "Apex", "Sierra", "Delta",
]
NAME_SUFFIXES = [
    "Labs", "Insights", "Data", "Analytics", "Capital", "Markets", "Metrics",
    "Signals", "Research", "Ventures", "Finance", "Pulse", "Edge", "Works",
]


def random_app_name(taken: set | None = None) -> str:
    """Random name like 'NovaInsights7' (number 1-10), not in `taken`."""
    taken = taken or set()
    for _ in range(1_000):
        name = (
            random.choice(NAME_PREFIXES)
            + random.choice(NAME_SUFFIXES)
            + str(random.randint(1, 10))
        )
        if name not in taken:
            return name
    raise RuntimeError("Could not find an unused random app name.")


def app_names(base: str | None, count: int, taken: set) -> list:
    """`count` unique names. With --app-name X and count > 1: X1, X2, ..."""
    if base:
        return [base] if count == 1 else [f"{base}{i}" for i in range(1, count + 1)]
    names: list = []
    for _ in range(count):
        name = random_app_name(taken | set(names))
        names.append(name)
    return names


def run_one_app(page, email: str, app_name: str, auth: str) -> tuple:
    """Create one app, register all APIs. Returns (api_key, failed_apis)."""
    api_key = create_app(page, email, app_name, auth)

    # After "Copy and close" the app page should be open.
    page.wait_for_load_state("networkidle", timeout=30_000)
    log(f"[*] App page: {page.url}")

    failed = register_all(page, APIS_TO_REGISTER)
    ok = len(APIS_TO_REGISTER) - len(failed)
    log(f"[+] Registered {ok}/{len(APIS_TO_REGISTER)} APIs for {app_name}")
    if failed:
        log("[!] Failed: " + ", ".join(failed))
    return api_key, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--app-name",
        default=None,
        help="app name (default: random, e.g. NovaInsights7)",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument(
        "--auth",
        default=AUTH_STRATEGY,
        choices=["Sandbox API Key", "Production API Key"],
        help="auth strategy for the new app",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="how many apps to create, one after another (default: 10)",
    )
    args = parser.parse_args()
    if args.count < 1:
        sys.exit("[x] --count must be at least 1")

    load_dotenv()
    email = os.getenv("ARGAAM_EMAIL", "").strip()
    password = os.getenv("ARGAAM_PASSWORD", "").strip()
    if not email or not password:
        sys.exit("[x] Set ARGAAM_EMAIL and ARGAAM_PASSWORD in .env")

    # Don't reuse names already saved for this account.
    taken = {
        k.get("app")
        for a in load_accounts() if a["email"] == email
        for k in a["api_keys"]
    }
    names = app_names(args.app_name, args.count, taken)

    results = []  # (app_name, api_key or None, failed_apis or error)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE_URL)
        page = context.new_page()
        try:
            login(page, email, password)
            for i, app_name in enumerate(names, 1):
                log(f"\n===== App {i}/{len(names)}: {app_name} =====")
                if i > 1:
                    back_to_applications(page)
                try:
                    api_key, failed = run_one_app(page, email, app_name, args.auth)
                    results.append((app_name, api_key, failed))
                except (PlaywrightTimeoutError, RuntimeError) as exc:
                    # Keep going with the next app; the next loop iteration
                    # returns to /apps (falls back to a direct goto).
                    log(f"[x] App {app_name} failed: {exc}")
                    screenshot(page, f"error_{app_name}")
                    results.append((app_name, None, str(exc)))
        except Exception:
            screenshot(page, "error")
            raise
        finally:
            context.close()
            browser.close()

            log("\n===== Summary =====")
            for app_name, api_key, failed in results:
                if api_key is None:
                    log(f"[x] {app_name}: FAILED ({failed})")
                elif failed:
                    log(f"[!] {app_name}: {api_key}  (not registered: {', '.join(failed)})")
                else:
                    log(f"[+] {app_name}: {api_key}")
            done = sum(1 for _, k, _ in results if k)
            log(f"\n{done}/{len(names)} apps created")


if __name__ == "__main__":
    try:
        main()
    except (PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[x] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
