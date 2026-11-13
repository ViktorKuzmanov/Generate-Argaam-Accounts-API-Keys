#!/usr/bin/env python3
"""
Generate fictional MENA finance-professional personas with an LLM (OpenAI or Gemini).

No extra packages needed (uses only the Python standard library).

Setup - paste your key into OPENAI_API_KEY or GEMINI_API_KEY below, or set one of these:
    export OPENAI_API_KEY="sk-..."
    export GEMINI_API_KEY="AIza..."

Usage:
    python3 generate_personas.py                     # 10 personas, auto-picks provider from the key you set
    python3 generate_personas.py -n 5                # 5 personas
    python3 generate_personas.py --provider gemini   # force a provider
    python3 generate_personas.py --model gpt-4o      # override the model

Each persona is a Python dictionary, e.g.
    persona = {"full_name": "...", "email": "fatimaalharbi56@kokimoki.shop",
               "password": "fatimaalharbi56@kokimoki.shop123!", ...}
New personas are ADDED to personas.json (existing entries are never deleted).
"""

import argparse
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request


def load_env_file(path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) -> None:
    """Load KEY=value lines from a .env file next to this script (Python doesn't do this by itself)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not os.getenv(key):
                os.environ[key] = value


load_env_file()

# ---- PASTE YOUR API KEY HERE (fill in ONE of them) --------------------------
OPENAI_API_KEY = ""   # e.g. "sk-..."
GEMINI_API_KEY = ""   # e.g. "AIza..."
# ------------------------------------------------------------------------------
OPENAI_API_KEY = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
}

ROYAL_NAMES = ["Al-Saud", "Al-Maktoum", "Al-Nahyan", "Al-Thani", "Al-Sabah", "Al-Khalifa", "Al-Said", "Al-Qasimi"]
SAFE_FAMILY_NAMES = ["Al-Qahtani", "Al-Otaibi", "Al-Ghamdi", "Al-Harbi", "Al-Shehri", "Al-Dosari", "Al-Mutairi", "Al-Zahrani"]

# Email = first name + family name + random number @ one of these domains
DOMAINS = [
    "anasasg.shop", "brimzokus.shop", "brzinski.shop", "databazi.shop", "dimdigfdgz.shop",
    "dimdimdim.shop", "dimdimeafgsz.shop", "dimdisgz.shop", "dimlvdigfdgz.shop", "fdsadd.shop",
    "funkystuffi.shop", "ideologijan.shop", "inkaytry.shop", "inkomitrite.shop", "jakaks.shop",
    "karmeinata.shop", "kikokokoas.shop", "klidsterios.shop", "klisdafa.shop", "kmertyst.shop",
    "kokimoki.shop",
]
PASSWORD_SUFFIX = "123!"
JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas.json")

FIELDS = ["full_name", "job_title", "company", "phone", "country", "use_case"]

PROMPT = """Generate {n} fictional, diverse personas as JSON. Return ONLY a JSON object of the form
{{"personas": [ {{...}}, ... ]}} where each persona has exactly these string keys:

- "full_name": a realistic Muslim full name (mix of male and female), Gulf/Arab style,
  e.g. first name + father's name + family name. Do NOT use royal family names
  (Al-Saud, Al-Maktoum, Al-Nahyan, Al-Thani, Al-Sabah, Al-Khalifa, Al-Said).
- "job_title": a finance role, e.g. Investment Analyst, Equity Research Analyst,
  Portfolio Manager, Credit Analyst, Private Equity Associate, Risk Analyst.
- "company": a made-up finance company name (must NOT be a real existing company).
- "phone": a mobile number with REAL DIGITS (never X placeholders) in international format
  matching the country, e.g. Saudi Arabia "+966 55 482 1937", UAE "+971 50 731 6624",
  Qatar "+974 5512 8834", Kuwait "+965 6623 1945", Bahrain "+973 3651 2208", Oman "+968 9234 5517".
- "country": Saudi Arabia or another MENA/GCC country (UAE, Qatar, Kuwait, Bahrain, Oman).
  Roughly 40% should be Saudi Arabia.
- "use_case": one sentence describing a specific, varied work use case for a finance tool
  (e.g. screening Tadawul stocks, tracking Sukuk yields, drafting investment memos).

Make every persona unique - no repeated names, companies, or use cases."""


def http_post_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"API error {e.code}: {e.read().decode('utf-8', 'replace')}")


def call_openai(prompt: str, model: str) -> str:
    key = OPENAI_API_KEY
    data = http_post_json(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        {"Authorization": f"Bearer {key}"},
    )
    return data["choices"][0]["message"]["content"]


def call_gemini(prompt: str, model: str) -> str:
    key = GEMINI_API_KEY
    data = http_post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0},
        },
        {"x-goog-api-key": key},
    )
    return data["candidates"][0]["content"]["parts"][0]["text"]


def parse_personas(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):  # strip markdown fences if the model added them
        raw = raw.strip("`").removeprefix("json").strip()
    data = json.loads(raw)
    items = data["personas"] if isinstance(data, dict) else data
    # keep only the expected keys, in a fixed order
    personas = [{k: str(p.get(k, "")).strip() for k in FIELDS} for p in items]
    for p in personas:
        # safety net: if the model still left X placeholders, fill them with random digits
        p["phone"] = re.sub(r"[Xx]", lambda _: str(random.randint(0, 9)), p["phone"])
        # safety net: swap out royal family names if the model used one anyway
        for royal in ROYAL_NAMES:
            if royal.lower() in p["full_name"].lower():
                p["full_name"] = re.sub(royal, random.choice(SAFE_FAMILY_NAMES), p["full_name"], flags=re.I)
    return personas


def make_email(full_name: str, taken: set) -> str:
    """first name + family name + random number, e.g. 'Fatima Ali Al-Harbi' -> fatimaalharbi56@kokimoki.shop"""
    parts = full_name.split()
    name = parts[0] + parts[-1] if len(parts) > 1 else parts[0]
    name = re.sub(r"[^a-z]", "", name.lower())
    while True:
        email = f"{name}{random.randint(10, 99)}@{random.choice(DOMAINS)}"
        if email not in taken:
            return email


def load_saved_personas() -> list:
    """Read personas.json. Never throws data away: stops if the file exists but can't be read."""
    if not os.path.exists(JSON_FILE) or os.path.getsize(JSON_FILE) == 0:
        return []
    try:
        with open(JSON_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        sys.exit(f"{JSON_FILE} is not valid JSON ({e}). Fix or move it first - not touching it.")
    if not isinstance(data, list):
        sys.exit(f"{JSON_FILE} should contain a JSON list. Not touching it.")
    return data


def save_personas(all_personas: list) -> None:
    """Write via a temp file and swap it in, so a crash mid-write can't corrupt the existing file."""
    tmp = JSON_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(all_personas, f, indent=2, ensure_ascii=False)
    os.replace(tmp, JSON_FILE)


def main():
    ap = argparse.ArgumentParser(description="Generate fictional MENA finance personas with an LLM.")
    ap.add_argument("-n", "--count", type=int, default=10)
    ap.add_argument("--provider", choices=["openai", "gemini"])
    ap.add_argument("--model")
    args = ap.parse_args()

    provider = args.provider or (
        "openai" if OPENAI_API_KEY else "gemini" if GEMINI_API_KEY else None
    )
    if not provider:
        sys.exit("No API key found. Paste your key into OPENAI_API_KEY or GEMINI_API_KEY at the top of generate_personas.py.")
    model = args.model or DEFAULT_MODELS[provider]

    saved = load_saved_personas()  # load first, so we never overwrite anything
    taken_emails = {p.get("email") for p in saved}

    print(f"Asking {provider} ({model}) for {args.count} personas...")
    prompt = PROMPT.format(n=args.count)
    raw = call_openai(prompt, model) if provider == "openai" else call_gemini(prompt, model)
    personas = []
    for p in parse_personas(raw)[: args.count]:
        email = make_email(p["full_name"], taken_emails)
        taken_emails.add(email)
        # each persona is one dictionary
        persona = {
            "full_name": p["full_name"],
            "email": email,
            "password": email + PASSWORD_SUFFIX,
            "job_title": p["job_title"],
            "company": p["company"],
            "phone": p["phone"],
            "country": p["country"],
            "use_case": p["use_case"],
        }
        personas.append(persona)
        print(persona)

    save_personas(saved + personas)  # old entries first, new ones added at the end
    print(f"\nAdded {len(personas)} personas to {JSON_FILE} (total now: {len(saved) + len(personas)})")
    return personas


if __name__ == "__main__":
    main()
