#!/usr/bin/env python3
"""Create the requested Argaam developer account using Playwright and Stealth.

Run from this directory after installing the dependencies:

    pip install -r requirements.txt
    python -m playwright install chromium
    python signUpAccount.py

The automation runs headlessly: no browser window is opened. It applies
playwright-stealth to help evade basic bot detection signatures, and integrates
with a CAPTCHA solving service (like SeleniumBase's sb.solve_captcha()) to
automatically solve Cloudflare Turnstile / human-checker challenges.
"""

import os
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth

import json
from pathlib import Path

# DATA_FILE = Path(__file__).parent / "data1.json"

# with open(DATA_FILE, encoding="utf-8") as f:
#     data = json.load(f)

# second = data[1]   # indexes start at 0, so [0] is the first item and [1] is the second

# ACCOUNT1 = {
#     "fullName" : second['full_name'],
#     "fullName" : second['full_name'],
#     "phoneNumber" : second['phone'],
#     "country" : second['country'],
#     "designation" : second['job_title'],
#     "companyName" : second['company'],
#     "useCase" : second['use_case']
# }

load_dotenv()

TARGET_URL = "https://developer.argaam.com"
REGISTER_URL = f"{TARGET_URL}/register"
IFRAME_URL = "https://api.argaam.com/dev-portal-register.html"

# One-time registration details.
# ACCOUNT = {
#     "fullName": "Traj Shahrani",
#     "email": "traj@ideologijan.shop",
#     "phoneNumber": "+966 52 778 4774",
#     "country": "Saudi Arabia",
#     "designation": "main investor",
#     "companyName": "Gemigjija",
#     "useCase": "Private equity",
# }

# CAPTCHA solving service configuration (2Captcha-compatible API)
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY", "").strip()
CAPTCHA_SERVICE_URL = os.getenv(
    "CAPTCHA_SERVICE_URL", "http://2captcha.com"
).strip()


def mask(value: str, show: int = 3) -> str:
    """Show only the first N characters of a secret for safe logging."""
    if not value:
        return "(empty)"
    return value[:show] + "*" * max(0, len(value) - show)


def proxy_settings() -> Optional[dict]:
    """Return optional Data Impulse proxy settings from .env."""
    login = os.getenv("DATA_IMPULSE_LOGIN", "").strip()
    password = os.getenv("DATA_IMPULSE_PASSWORD", "").strip()
    host = os.getenv("DATA_IMPULSE_PROXY_HOST", "").strip()
    port = os.getenv("DATA_IMPULSE_PROXY_PORT", "").strip()

    print("\n" + "=" * 60)
    print("[*] PROXY CONFIGURATION")
    print("=" * 60)

    if not any((login, password, host, port)):
        print("[!] No Data Impulse proxy credentials found in .env")
        print("    -> Connecting directly (no proxy)")
        print("=" * 60 + "\n")
        return None

    if not all((login, password, host, port)):
        raise ValueError(
            "Proxy configuration is incomplete. Set all DATA_IMPULSE_* values "
            "or remove them to connect directly."
        )

    print(f"    Proxy Type:    Data Impulse Residential Proxy")
    print(f"    Proxy Host:    {host}")
    print(f"    Proxy Port:    {port}")
    print(f"    Proxy Login:   {mask(login)}")
    print(f"    Proxy Password: {mask(password)}")
    print(f"    Proxy URL:     http://{mask(login)}:{mask(password)}@{host}:{port}")
    print("=" * 60 + "\n")

    return {
        "server": f"http://{host}:{port}",
        "username": login,
        "password": password,
    }


def fill_form(frame, acc1) -> None:
    """Fill the registration form embedded by the developer portal."""
    for field_id, value in acc1.items():
        frame.locator(f"#{field_id}").fill(value)


def detect_captcha(page) -> bool:
    """Check the page for CAPTCHA / human-checker indicators. Prints if found."""
    try:
        content = page.content().lower()
    except Exception:
        return False

    captcha_keywords = [
        "captcha",
        "confirm you are human",
        "choose all the chairs",
        "human verification",
        "verify you are human",
        "robot",
        "automated",
        "turnstile",
        "cloudflare",
    ]

    for keyword in captcha_keywords:
        if keyword in content:
            print(f"[!] CAPTCHA / human checker detected: '{keyword}'")
            return True
    return False


def solve_captcha_with_service(sitekey: str, page_url: str) -> Optional[str]:
    """
    Solve a Cloudflare Turnstile CAPTCHA using a 2Captcha-compatible service.

    Returns the CAPTCHA response token string, or None if solving failed.
    """
    if not CAPTCHA_API_KEY:
        print(
            "[!] No CAPTCHA_API_KEY set in .env. Cannot auto-solve CAPTCHA. "
            "Set CAPTCHA_API_KEY=your_2captcha_key to enable auto-solving."
        )
        return None

    # Step 1: Submit the CAPTCHA to the solving service
    submit_url = f"{CAPTCHA_SERVICE_URL.rstrip('/')}/in.php"
    payload = {
        "key": CAPTCHA_API_KEY,
        "method": "turnstile",
        "sitekey": sitekey,
        "pageurl": page_url,
        "json": 1,
    }

    try:
        resp = requests.post(submit_url, data=payload, timeout=30)
        result = resp.json()
    except Exception as exc:
        print(f"[!] Failed to submit CAPTCHA to solving service: {exc}")
        return None

    if result.get("status") != 1:
        print(f"[!] CAPTCHA submission failed: {result.get('request', 'unknown error')}")
        return None

    captcha_id = result["request"]
    print(f"[*] CAPTCHA submitted to solving service (ID: {captcha_id})")

    # Step 2: Poll the service for the solution
    result_url = f"{CAPTCHA_SERVICE_URL.rstrip('/')}/res.php"
    for attempt in range(30):  # Poll up to ~3 minutes
        try:
            resp = requests.get(
                result_url,
                params={
                    "key": CAPTCHA_API_KEY,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1,
                },
                timeout=30,
            )
            result = resp.json()
        except Exception as exc:
            print(f"[!] Error polling CAPTCHA result: {exc}")
            time.sleep(5)
            continue

        if result.get("status") == 1:
            token = result["request"]
            print(f"[+] CAPTCHA solved successfully (token received)")
            return token
        elif result.get("request") == "CAPCHA_NOT_READY":
            print(f"    [!] CAPTCHA not ready yet, waiting... (attempt {attempt + 1}/30)")
            time.sleep(6)
        else:
            print(f"[!] CAPTCHA solving failed: {result.get('request', 'unknown error')}")
            return None

    print("[!] CAPTCHA solving timed out after 3 minutes")
    return None


def extract_turnstile_sitekey(frame) -> Optional[str]:
    """Extract the Cloudflare Turnstile sitekey from inside the iframe."""
    try:
        sitekey = frame.evaluate("""() => {
            const widgets = document.querySelectorAll('[data-sitekey]');
            for (const w of widgets) {
                const key = w.getAttribute('data-sitekey');
                if (key) return key;
            }
            return null;
        }""")
        return sitekey
    except Exception as exc:
        print(f"[!] Failed to extract Turnstile sitekey: {exc}")
        return None


def apply_turnstile_token(frame, token: str) -> None:
    """Inject the solved CAPTCHA token into the iframe page and trigger the callback."""
    try:
        frame.evaluate("""(token) => {
            // Look for Turnstile callback
            const widgets = document.querySelectorAll('[data-sitekey]');
            for (const w of widgets) {
                const callback = w.getAttribute('data-callback');
                if (callback) {
                    const fn = window[callback];
                    if (typeof fn === 'function') {
                        fn(token);
                        return;
                    }
                }
            }
            // Fallback: look for cf-turnstile-response input
            const input = document.querySelector('input[name="cf-turnstile-response"]');
            if (input) {
                input.value = token;
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""", token)
        print("[+] CAPTCHA token applied to page")
    except Exception as exc:
        print(f"[!] Failed to apply CAPTCHA token: {exc}")


def solve_captcha(page, frame) -> bool:
    """
    Detect and solve CAPTCHA on the page.

    Mirrors SeleniumBase's sb.solve_captcha():
    1. Detects CAPTCHA presence
    2. Extracts the sitekey from inside the iframe
    3. Sends to solving service
    4. Applies the solution token

    Returns True if CAPTCHA was solved or not present, False if unsolvable.
    """
    if not detect_captcha(page):
        return True  # No CAPTCHA present

    print("[!] CAPTCHA detected — attempting to solve...")

    # Extract the Turnstile sitekey from inside the iframe
    sitekey = extract_turnstile_sitekey(frame)
    if not sitekey:
        print("[!] Could not extract Turnstile sitekey. Cannot auto-solve.")
        return False

    print(f"[*] Extracted Turnstile sitekey: {sitekey[:20]}...")

    # Solve via the CAPTCHA service (use iframe URL as pageurl)
    token = solve_captcha_with_service(sitekey, IFRAME_URL)
    if not token:
        return False

    # Apply the token to the page inside the iframe
    apply_turnstile_token(frame, token)
    # Give the page a moment to process the CAPTCHA token
    page.wait_for_timeout(2000)
    return True


def main() -> None:
    
    for i in range(3):
        # sozdaj go tuka acc1 dictionary so e ustvari ACCOUNT oti go predavam dole

        DATA_FILE = Path(__file__).parent / "personas.json"

        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)

        second = data[i]   

        acc1 = {
            "fullName" : second['full_name'],
            "email" : second['email'],
            "phoneNumber" : second['phone'],
            "country" : second['country'],
            "designation" : second['job_title'],
            "companyName" : second['company'],
            "useCase" : second['use_case']
        }

        print(acc1)

        print(f"[*] Opening {REGISTER_URL}")

        # Get proxy config once (also prints the proxy banner)
        proxies = proxy_settings()
        print(f"    Proxy: {'Enabled (Data Impulse Residential)' if proxies else 'Disabled (direct connection)'}")

        with sync_playwright() as playwright:
            print("[*] Launching Chromium browser (headless=True)")
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                ],
            )
            print("[+] Browser launched")

            print("[*] Creating browser context with Data Impulse residential proxy")
            context = browser.new_context(
                proxy=proxies,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
            print("[+] Browser context created with proxy routing")

            page = context.new_page()
            print("[+] New page opened")

            # Apply stealth to patch navigator.webdriver and other fingerprinting variables
            stealth.Stealth().apply_stealth_sync(page)
            print("[+] Playwright stealth applied (navigator.webdriver patched)")

            try:
                print(f"[*] Navigating to {REGISTER_URL} via proxy...")
                page.goto(REGISTER_URL, wait_until="domcontentloaded", timeout=60_000)
                print(f"[+] Page loaded: {REGISTER_URL}")

                # Wait for the registration iframe to load
                iframe_locator = page.frame_locator("iframe[data-testid='prose-iframe']")
                iframe_locator.locator("#fullName").wait_for(state="visible", timeout=30_000)
                print("[+] Registration iframe loaded")

                # Find the actual Frame object for evaluate() calls
                frame_obj = None
                for f in page.frames:
                    if "dev-portal-register" in f.url:
                        frame_obj = f
                        break

                if not frame_obj:
                    print("[!] Could not locate registration iframe frame object.")
                    raise SystemExit(1)

                print(f"[*] Found iframe: {frame_obj.url}")

                # Solve CAPTCHA if present (like SeleniumBase's sb.solve_captcha())
                if not solve_captcha(page, frame_obj):
                    print("[x] Cannot proceed — CAPTCHA could not be solved.")
                    raise SystemExit(1)

                # Fill the form inside the iframe
                print("[*] Filling registration form inside iframe...")
                fill_form(iframe_locator, acc1)
                print(f"[+] Form filled with account data")

                submit = iframe_locator.locator("#submitBtn")
                submit.wait_for(state="visible", timeout=30_000)
                print("[*] Waiting for Create Account button to become available...")

                print("[*] Clicking Create Account button...")
                submit.click(timeout=120_000)
                page.wait_for_timeout(3_000)
                print("[+] Create Account clicked successfully.")
                page.wait_for_timeout(10_000)
                print("[+] Account creation completed.")
            finally:
                context.close()
                browser.close()
                print("[*] Browser closed")


if __name__ == "__main__":
    try:
        main()
    except (PlaywrightTimeoutError, ValueError) as exc:
        print(f"[x] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
