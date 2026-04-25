#!/usr/bin/env python3
"""
Verify competitor Page IDs and provide a small evidence report.

This script:
- Resolves each page_id to page name via Graph API
- Tries an ads_archive probe per page_id to see if it returns any ads

Run:
  python3 scripts/verify_competitors.py
"""

import json
import os
from pathlib import Path
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.example", override=False)

TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("META_ACCESS_TOKEN missing. Put it in .env first.")

COMPETITORS_PATH = ROOT / "data" / "competitors.json"
OUT_DIR = ROOT / "output" / "reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_VERSION = "v25.0"


def direct_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    return s


def main() -> None:
    competitors = json.loads(COMPETITORS_PATH.read_text())
    competitors.pop("_search_term_fallbacks", None)

    session = direct_session()
    today = datetime.now().date()
    date_min = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    date_max = today.strftime("%Y-%m-%d")

    results = []
    for brand, page_id in competitors.items():
        ads_resp = session.get(
            f"https://graph.facebook.com/{API_VERSION}/ads_archive",
            params={
                "access_token": TOKEN,
                "ad_reached_countries": "['US']",
                # Use ALL to verify the page id is valid even if nothing is ACTIVE today.
                "ad_active_status": "ALL",
                "ad_delivery_date_min": date_min,
                "ad_delivery_date_max": date_max,
                "search_page_ids": str(page_id),
                "fields": "id,page_name,ad_delivery_start_time,ad_snapshot_url,ad_creative_bodies",
                "limit": 5,
            },
            timeout=30,
        )
        ads_json = ads_resp.json()
        ads_count = len(ads_json.get("data") or []) if ads_resp.status_code == 200 else 0
        resolved_name = None
        if ads_resp.status_code == 200 and ads_count:
            resolved_name = (ads_json.get("data") or [{}])[0].get("page_name")

        results.append(
            {
                "brand_label": brand,
                "page_id": page_id,
                "resolved_page_name": resolved_name,
                "ads_archive_ok": ads_resp.status_code == 200,
                "ads_returned": ads_count,
                "ads_archive_error": None if ads_resp.status_code == 200 else ads_json.get("error", ads_json),
            }
        )

    ts = int(datetime.now().timestamp())
    out_path = OUT_DIR / f"competitor_page_id_check_{ts}.json"
    out_path.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "results": results}, indent=2))

    print(f"Wrote report: {out_path}")
    for r in results:
        ok = "OK" if (r["ads_archive_ok"] and r["ads_returned"] > 0) else "CHECK"
        print(
            f"[{ok}] {r['brand_label']} → page_id={r['page_id']} "
            f"name={r['resolved_page_name']!r} ads={r['ads_returned']}"
        )


if __name__ == "__main__":
    main()

