#!/usr/bin/env python3
"""
Discover likely advertiser pages for competitor brands.

Why: `search_page_ids` is the only reliable way to target a competitor, but brand
Facebook Page IDs often don't match the advertiser page ID Meta uses in Ad Library.

This script searches ads_archive by keyword and reports the top page_name/page_id
pairs observed, so you can paste the correct IDs into data/competitors.json.

Run:
  python3 scripts/discover_competitor_pages.py
"""

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.example", override=False)

TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("META_ACCESS_TOKEN missing. Put it in .env first.")

API_VERSION = "v25.0"
OUT_DIR = ROOT / "output" / "reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)

COMPETITORS_PATH = ROOT / "data" / "competitors.json"


def direct_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    return s


def main() -> None:
    competitors = json.loads(COMPETITORS_PATH.read_text())
    competitors.pop("_search_term_fallbacks", None)

    # Search with brand labels + a couple common variants
    queries: dict[str, list[str]] = {}
    for label in competitors.keys():
        base = label.split("(")[0].strip()
        variants = [base]
        if "AG1" in label:
            variants += ["Athletic Greens", "AG 1"]
        queries[label] = [q for q in variants if q]

    s = direct_session()
    today = datetime.now().date()
    date_min = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    date_max = today.strftime("%Y-%m-%d")

    results = {}
    for competitor, qlist in queries.items():
        counter = Counter()
        samples = defaultdict(list)
        for q in qlist:
            r = s.get(
                f"https://graph.facebook.com/{API_VERSION}/ads_archive",
                params={
                    "access_token": TOKEN,
                    "ad_reached_countries": "['US']",
                    "ad_active_status": "ALL",
                    "ad_delivery_date_min": date_min,
                    "ad_delivery_date_max": date_max,
                    "search_terms": q,
                    "search_type": "KEYWORD_EXACT_PHRASE",
                    "fields": "id,page_id,page_name,ad_delivery_start_time,ad_snapshot_url,ad_creative_bodies",
                    "limit": 50,
                },
                timeout=30,
            )
            data = r.json()
            if r.status_code != 200:
                results[competitor] = {"error": data.get("error", data)}
                break
            for ad in data.get("data") or []:
                pid = str(ad.get("page_id", ""))
                pname = str(ad.get("page_name", ""))
                if not pid or not pname:
                    continue
                key = f"{pname}::{pid}"
                counter[key] += 1
                if len(samples[key]) < 2:
                    bodies = ad.get("ad_creative_bodies") or []
                    samples[key].append(
                        {
                            "ad_id": ad.get("id"),
                            "ad_delivery_start_time": ad.get("ad_delivery_start_time"),
                            "ad_snapshot_url": ad.get("ad_snapshot_url"),
                            "ad_copy_excerpt": (bodies[0][:160] + "…") if bodies else "",
                        }
                    )

        top = []
        for key, c in counter.most_common(10):
            pname, pid = key.split("::", 1)
            top.append(
                {
                    "page_name": pname,
                    "page_id": pid,
                    "ads_seen_in_sample": c,
                    "examples": samples[key],
                }
            )
        results[competitor] = {"queries": qlist, "top_candidates": top}

    ts = int(datetime.now().timestamp())
    out_path = OUT_DIR / f"competitor_page_discovery_{ts}.json"
    out_path.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "results": results}, indent=2))
    print(f"Wrote: {out_path}")

    # Print human-readable summary
    for comp, payload in results.items():
        print(f"\n== {comp} ==")
        if "error" in payload:
            print("ERROR:", payload["error"])
            continue
        for cand in payload["top_candidates"][:5]:
            print(f"- {cand['page_name']}  (page_id={cand['page_id']})  ads_seen={cand['ads_seen_in_sample']}")


if __name__ == "__main__":
    main()

