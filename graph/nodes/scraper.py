"""
Hunter Node — fetches long-running competitor ads from Meta Ad Library API.

Long-running is defined as: ad started >= 30 days ago AND is currently ACTIVE.
Strategy: cast a 90-day net via ad_delivery_date_min, then post-filter for 30+ days.
"""

import json
import os
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from graph.state import AgentState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}/ads_archive"
LONG_RUNNING_DAYS = 15
LOOKBACK_DAYS = 760  # wide net; post-filter handles the 60-day threshold
PAGE_LIMIT = 200    # items per API page (max 500)
MAX_ADS_DEFAULT = 250  # hard cap to keep runs fast; env override via META_MAX_ADS

FIELDS = ",".join([
    "id",
    "ad_delivery_start_time",
    "ad_creative_bodies",
    "ad_creative_link_captions",
    "ad_creative_link_descriptions",
    "ad_creative_link_titles",
    "ad_snapshot_url",
    "page_name",
    "page_id",
])

COMPETITORS_PATH = Path(__file__).parent.parent.parent / "data" / "competitors.json"
ADS_EXPORT_DIR = Path(__file__).parent.parent.parent / "output" / "ads"
ADS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

def _direct_session() -> requests.Session:
    """
    Create a Session that ignores proxy env vars.

    Some environments set HTTPS_PROXY/HTTP_PROXY which can break calls to
    graph.facebook.com (e.g., Tunnel connection failed: 403).
    """
    s = requests.Session()
    s.trust_env = False
    return s


def _load_competitors() -> dict:
    with open(COMPETITORS_PATH) as f:
        return json.load(f)


def _is_long_running(ad: dict, cutoff: datetime) -> bool:
    """Return True if the ad started on or before the 60-day cutoff."""
    start_raw = ad.get("ad_delivery_start_time")
    if not start_raw:
        return False
    try:
        # API returns dates as "YYYY-MM-DD" strings
        start_dt = datetime.strptime(start_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return start_dt <= cutoff
    except ValueError:
        return False


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _page_name_matches_competitors(page_name: str, competitor_terms: list[str]) -> bool:
    pn = _norm(page_name)
    if not pn:
        return False
    for t in competitor_terms:
        tt = _norm(t)
        if tt and tt in pn:
            return True
    return False


def _fetch_ads_for_page_ids(
    access_token: str,
    page_ids: list[str],
    date_min: str,
    date_max: str,
    cutoff_dt: datetime,
    max_ads: int,
) -> list[dict]:
    """Paginate through all results for a batch of up to 10 page IDs."""
    session = _direct_session()
    page_limit = int(os.environ.get("META_PAGE_LIMIT", str(PAGE_LIMIT)))
    ad_active_status = os.environ.get("META_AD_ACTIVE_STATUS", "ACTIVE").strip().upper()
    if ad_active_status not in ("ACTIVE", "ALL", "INACTIVE"):
        ad_active_status = "ACTIVE"

    params = {
        "access_token": access_token,
        "ad_reached_countries": "['US']",
        "ad_active_status": ad_active_status,
        "ad_delivery_date_min": date_min,
        "ad_delivery_date_max": date_max,
        "search_page_ids": ",".join(page_ids),
        "fields": FIELDS,
        "limit": page_limit,
    }

    ads: list[dict] = []
    url = BASE_URL

    while url:
        resp = session.get(url, params=params if url == BASE_URL else None, timeout=30)
        data = resp.json()

        if resp.status_code >= 400:
            err = data.get("error", data)
            print(f"[Scraper] HTTP {resp.status_code} error (page_ids): {err}")
            resp.raise_for_status()

        if "error" in data:
            print(f"[Scraper] API error: {data['error']}")
            break

        for ad in data.get("data", []):
            if _is_long_running(ad, cutoff_dt):
                ads.append(ad)
                if len(ads) >= max_ads:
                    url = None
                    break

        # Follow pagination cursor; clear params so next URL is used as-is
        url = data.get("paging", {}).get("next")
        params = None

        # Respect Meta rate limits (gentle backoff between pages)
        if url:
            time.sleep(0.5)

    return ads


def _fetch_ads_by_search_terms(
    access_token: str,
    search_terms: list[str],
    date_min: str,
    date_max: str,
    cutoff_dt: datetime,
    max_ads: int,
    competitor_terms: list[str] | None = None,
) -> list[dict]:
    """Fallback: search by keyword when page IDs return sparse results."""
    session = _direct_session()
    ads: list[dict] = []

    for term in search_terms:
        page_limit = int(os.environ.get("META_PAGE_LIMIT", str(PAGE_LIMIT)))
        params = {
            "access_token": access_token,
            "ad_reached_countries": "['US']",
            "ad_active_status": "ACTIVE",
            "ad_delivery_date_min": date_min,
            "ad_delivery_date_max": date_max,
            "search_terms": term,
            "search_type": "KEYWORD_EXACT_PHRASE",
            "fields": FIELDS,
            "limit": page_limit,
        }

        url = BASE_URL
        term_ads: list[dict] = []
        pages_scanned = 0
        max_pages = int(os.environ.get("META_MAX_PAGES_PER_TERM", "3")) if competitor_terms else int(
            os.environ.get("META_MAX_PAGES_PER_TERM_GENERIC", "20")
        )

        while url:
            pages_scanned += 1
            resp = session.get(url, params=params if url == BASE_URL else None, timeout=30)
            data = resp.json()

            if resp.status_code >= 400:
                err = data.get("error", data)
                print(f"[Scraper] HTTP {resp.status_code} error (term='{term}'): {err}")
                resp.raise_for_status()

            if "error" in data:
                print(f"[Scraper] API error on term '{term}': {data['error']}")
                break

            for ad in data.get("data", []):
                if _is_long_running(ad, cutoff_dt):
                    if competitor_terms:
                        if not _page_name_matches_competitors(ad.get("page_name", ""), competitor_terms):
                            continue
                    term_ads.append(ad)
                    if len(ads) + len(term_ads) >= max_ads:
                        url = None
                        break

            url = data.get("paging", {}).get("next")
            params = None
            if url and pages_scanned < max_pages:
                time.sleep(0.5)
            elif pages_scanned >= max_pages:
                url = None

        print(f"[Scraper] '{term}' → {len(term_ads)} long-running ads")
        ads.extend(term_ads)
        if len(ads) >= max_ads:
            break

    return ads


def scraper_node(state: AgentState) -> dict:
    """
    LangGraph node: Hunter.

    Fetches ACTIVE ads running for 60+ days from Meta Ad Library.
    Batches page IDs in groups of 10 (API limit).
    Falls back to keyword search if page-ID results are sparse.

    Set ``USE_MOCK_META_ADS=true`` to skip Meta and load ``data/mock_ads.json``
    (and ``data/mock_ads_rescrape.json`` on analyst-triggered re-scrapes).
    """
    access_token = os.environ.get("META_ACCESS_TOKEN", "")
    if not access_token:
        raise EnvironmentError("META_ACCESS_TOKEN is not set in environment.")

    # Meta validates ad_delivery_date_max against "Today" in its server timezone.
    # Using UTC can roll the date forward and cause: "ad_delivery_date_max is invalid".
    today_utc = datetime.now(tz=timezone.utc)
    today_local = datetime.now()
    lookback = int(os.environ.get("META_LOOKBACK_DAYS", str(LOOKBACK_DAYS)))
    long_running_days = int(os.environ.get("META_LONG_RUNNING_DAYS", str(LONG_RUNNING_DAYS)))
    max_ads = int(os.environ.get("META_MAX_ADS", str(MAX_ADS_DEFAULT)))
    date_min = (today_local - timedelta(days=lookback)).strftime("%Y-%m-%d")
    date_max = today_local.strftime("%Y-%m-%d")
    cutoff_dt = today_utc - timedelta(days=long_running_days)

    competitors = _load_competitors()
    competitor_labels = list(competitors.keys())

    # Primary path: batch advertiser page IDs in groups of 10.
    # We intentionally do NOT do keyword-based discovery here (too noisy).
    page_ids = [pid for pid in competitors.values() if str(pid).strip().isdigit()]
    if not page_ids:
        raise ValueError(
            "No competitor page IDs set in data/competitors.json. "
            "Paste Meta Ad Library advertiser page IDs (numbers) for each competitor."
        )

    all_ads: list[dict] = []
    for i in range(0, len(page_ids), 10):
        batch = page_ids[i : i + 10]
        batch_ads = _fetch_ads_for_page_ids(access_token, batch, date_min, date_max, cutoff_dt, max_ads=max_ads)
        print(f"[Scraper] Batch {i//10 + 1}: {len(batch_ads)} long-running ads")
        all_ads.extend(batch_ads)
        if len(all_ads) >= max_ads:
            break

    raw_ads = all_ads

    # Deduplicate by ad ID
    seen: set[str] = set()
    deduped: list[dict] = []
    for ad in raw_ads:
        ad_id = ad.get("id", "")
        if ad_id and ad_id not in seen:
            seen.add(ad_id)
            deduped.append(ad)

    print(f"[Scraper] Total unique long-running ads fetched: {len(deduped)}")
    if len(deduped) == 0:
        raise ValueError(
            "Scraper returned 0 ads for the configured competitor page IDs. "
            "Try widening scrape settings (for the demo): META_AD_ACTIVE_STATUS=ALL and META_LONG_RUNNING_DAYS=1, "
            "or verify the advertiser page IDs are correct in data/competitors.json."
        )

    # Export raw scrape for case-study evidence
    try:
        import time as _time
        ts = int(_time.time())
        export_path = ADS_EXPORT_DIR / f"scraped_ads_iter{state.get('iteration_count', 0)+1}_{ts}.json"
        export_path.write_text(json.dumps(deduped, indent=2))
        print(f"[Scraper] Exported raw ads → {export_path}")
    except Exception as e:
        print(f"[Scraper] Export warning: failed to write output/ads export: {e}")

    return {
        "raw_ads": deduped,
        "rescrape_needed": False,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }
