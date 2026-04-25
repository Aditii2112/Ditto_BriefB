#!/usr/bin/env python3
"""Quick check: Meta token + Gemini key work. Run from repo root: python3 scripts/smoke_test_env.py"""

import json
from pathlib import Path
import os
import sys

import requests
from dotenv import load_dotenv

root = Path(__file__).resolve().parent.parent
load_dotenv(root / ".env")
load_dotenv(root / ".env.example", override=False)

meta = os.environ.get("META_ACCESS_TOKEN", "").strip()
gemini = os.environ.get("GEMINI_API_KEY", "").strip()
use_mock = os.environ.get("USE_MOCK_META_ADS", "").strip().lower() in ("1", "true", "yes")

errors = 0

if use_mock:
    mock_path = root / "data" / "mock_ads.json"
    if mock_path.is_file():
        n = len(json.loads(mock_path.read_text()))
        print(f"OK: Mock ads fixture — {n} rows in data/mock_ads.json (Meta API skipped)")
    else:
        print("FAIL: USE_MOCK_META_ADS=true but data/mock_ads.json is missing")
        errors += 1
elif not meta:
    print("FAIL: META_ACCESS_TOKEN is empty (or set USE_MOCK_META_ADS=true)")
    errors += 1
else:
    r = requests.get(
        "https://graph.facebook.com/v25.0/me",
        params={"access_token": meta, "fields": "id,name"},
        timeout=30,
    )
    data = r.json()
    if r.status_code == 200 and "id" in data:
        print("OK: Meta token valid — Graph API /me succeeded")
        print(f"     User id: {data.get('id')}, name: {data.get('name', '')[:40]}")
    else:
        print("FAIL: Meta token —", r.status_code, data.get("error", data))
        errors += 1

if not gemini:
    print("FAIL: GEMINI_API_KEY is empty")
    errors += 1
else:
    try:
        from google import genai

        client = genai.Client(api_key=gemini)
        # Use a current model id (1.5 ids are often retired on the v1 API)
        resp = client.models.generate_content(
            model="gemini-flash-latest",
            contents="Reply with exactly: pong",
        )
        text = (resp.text or "").strip().lower()
        if "pong" in text:
            print("OK: Gemini API — generate_content succeeded")
        else:
            print("WARN: Gemini replied unexpectedly:", text[:80])
    except Exception as e:
        print("FAIL: Gemini —", e)
        errors += 1

# Light Ad Library probe (needs ads_read + ID verification)
if meta and not use_mock:
    r2 = requests.get(
        "https://graph.facebook.com/v25.0/ads_archive",
        params={
            "access_token": meta,
            "ad_reached_countries": "['US']",
            "ad_active_status": "ACTIVE",
            "search_terms": "coffee",
            "fields": "id,page_name",
            "limit": 1,
        },
        timeout=30,
    )
    d2 = r2.json()
    if r2.status_code == 200 and "data" in d2:
        n = len(d2.get("data") or [])
        print(f"OK: Ad Library ads_archive — returned {n} sample ad(s)")
    else:
        err = d2.get("error", d2)
        print("FAIL: ads_archive —", r2.status_code, err)
        errors += 1

print("---")
print("Smoke test:", "all OK" if errors == 0 else f"{errors} failure(s)")
sys.exit(1 if errors else 0)
