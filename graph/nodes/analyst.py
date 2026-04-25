"""
Analyst Node — "The Analyst".

Feeds raw competitor ad copy + snapshot URLs into Gemini for structured
pattern extraction. If fewer than 3 distinct hook types are found and we
have not yet hit the iteration ceiling, signals a re-scrape.
"""

import json
import os
import textwrap
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, field_validator
from typing import Literal

from graph.state import AgentState

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_HOOK_TYPES = 3          # rescrape if fewer distinct hooks found
MAX_ADS_PER_BATCH = 30      # keep prompt size manageable
MAX_ITERATIONS = 3          # ceiling on scrape-analyse cycles

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
REPORTS_DIR = Path(__file__).parent.parent.parent / "output" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Pydantic schema for a single ad's pattern (used for local validation)
# ---------------------------------------------------------------------------

class AdPattern(BaseModel):
    ad_id: str
    brand: str
    hook_format: Literal["question", "life_hack", "us_vs_them", "stat_lead", "story"]
    offer_framing: Literal[
        "first_month_free", "percent_off", "bundle_save", "free_trial", "none"
    ]
    emotional_angle: Literal[
        "fomo", "aspiration", "convenience", "authority", "social_proof"
    ]
    copy_length: Literal["short", "medium", "long"]
    cta_style: str
    visual_theme: str
    ad_copy_excerpt: str

    @field_validator("hook_format", "offer_framing", "emotional_angle", "copy_length", mode="before")
    @classmethod
    def lowercase_strip(cls, v: Any) -> str:
        return str(v).lower().strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    path = PROMPTS_DIR / "analyst_system.txt"
    return path.read_text()


def _build_user_message(ads: list[dict]) -> str:
    """Construct the user-turn message containing ad data for Gemini."""
    lines = [
        f"Analyse the following {len(ads)} long-running competitor ads. "
        "For each ad, I provide the brand name, ad copy, and the snapshot URL "
        "so you can also assess the visual creative.\n"
    ]

    for i, ad in enumerate(ads, 1):
        bodies = ad.get("ad_creative_bodies") or []
        copy_text = " | ".join(bodies) if bodies else "(no copy text available)"
        snapshot = ad.get("ad_snapshot_url", "")
        brand = ad.get("page_name") or ad.get("funding_entity") or "Unknown"

        lines.append(f"--- AD {i} ---")
        lines.append(f"ID: {ad.get('id', 'unknown')}")
        lines.append(f"Brand: {brand}")
        lines.append(f"Copy: {textwrap.shorten(copy_text, width=500, placeholder='...')}")
        if snapshot:
            lines.append(f"Snapshot URL: {snapshot}")
        lines.append("")

    return "\n".join(lines)


def _call_gemini(system_prompt: str, user_message: str) -> dict:
    """Call Gemini and return parsed JSON response."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set in environment.")

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL_ANALYST", "gemini-2.5-flash"),
        contents=user_message,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.2,    # low temp for consistent taxonomy
        ),
    )

    raw = response.text.strip()

    # Strip markdown code fences if Gemini wraps the JSON
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


def _extract_rescrape_keywords(raw_ads: list[dict]) -> list[str]:
    """Generate broader fallback search terms from the existing scrape."""
    terms = [
        "health supplement subscription monthly",
        "personalized nutrition plan subscription",
        "greens powder daily routine",
        "vitamin pack subscription wellness",
        "protein shake subscription fitness",
    ]
    return terms


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def analyst_node(state: AgentState) -> dict:
    """
    LangGraph node: Analyst.

    Analyses raw_ads with Gemini to produce extracted_insights.
    Sets rescrape_needed=True if fewer than MIN_HOOK_TYPES distinct hook
    formats are found and iteration_count is below MAX_ITERATIONS.
    """
    raw_ads = state.get("raw_ads", [])

    if not raw_ads:
        print("[Analyst] No ads to analyse — triggering re-scrape")
        return {
            "extracted_insights": {},
            "rescrape_needed": True,
            "rescrape_keywords": _extract_rescrape_keywords(raw_ads),
        }

    # Cap to MAX_ADS_PER_BATCH to keep prompt tokens manageable
    ads_to_analyse = raw_ads[:MAX_ADS_PER_BATCH]
    print(f"[Analyst] Analysing {len(ads_to_analyse)} ads (of {len(raw_ads)} total)")

    system_prompt = _load_system_prompt()
    user_message = _build_user_message(ads_to_analyse)

    try:
        insights = _call_gemini(system_prompt, user_message)
    except Exception as e:
        print(f"[Analyst] Gemini call failed: {e}")
        raise

    # Normalize frequency_pct to 0-1 (models sometimes return 0-100).
    for wp in insights.get("winning_patterns", []) or []:
        try:
            fp = float(wp.get("frequency_pct", 0.0))
            if fp > 1.0:
                wp["frequency_pct"] = fp / 100.0
        except Exception:
            pass

    # Validate and enrich individual ad patterns via Pydantic
    validated_ads = []
    for ad_raw in insights.get("ads", []):
        try:
            pattern = AdPattern(**ad_raw)
            validated_ads.append(pattern.model_dump())
        except Exception as e:
            print(f"[Analyst] Validation warning for ad {ad_raw.get('ad_id')}: {e}")
            validated_ads.append(ad_raw)  # keep unvalidated rather than drop

    insights["ads"] = validated_ads

    # -----------------------------------------------------------------------
    # Export a compact "pattern report" for the case study (JSON + Markdown)
    # -----------------------------------------------------------------------
    try:
        analysed = insights.get("ads", []) or []
        total = len(analysed)
        hook_counts = Counter([a.get("hook_format", "unknown") for a in analysed])
        emo_counts = Counter([a.get("emotional_angle", "unknown") for a in analysed])
        combo_counts = Counter(
            [(a.get("hook_format", "unknown"), a.get("emotional_angle", "unknown")) for a in analysed]
        )

        examples_by_combo: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for a in analysed:
            key = (a.get("hook_format", "unknown"), a.get("emotional_angle", "unknown"))
            if len(examples_by_combo[key]) < 3:
                examples_by_combo[key].append(
                    {
                        "ad_id": a.get("ad_id"),
                        "brand": a.get("brand"),
                        "ad_copy_excerpt": a.get("ad_copy_excerpt"),
                    }
                )

        ts = int(time.time())
        iteration = state.get("iteration_count", 1)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration,
            "total_ads_analysed": total,
            "hook_format_counts": dict(hook_counts),
            "emotional_angle_counts": dict(emo_counts),
            "top_combinations": [
                {
                    "hook_format": hf,
                    "emotional_angle": ea,
                    "count": c,
                    "frequency_pct": (c / total) if total else 0.0,
                    "examples": examples_by_combo[(hf, ea)],
                }
                for (hf, ea), c in combo_counts.most_common(5)
            ],
            "winning_patterns": insights.get("winning_patterns", []),
            "pattern_gaps": insights.get("pattern_gaps", []),
        }

        json_path = REPORTS_DIR / f"pattern_report_iter{iteration}_{ts}.json"
        json_path.write_text(json.dumps(report, indent=2))

        md_lines = [
            "# DITTO Pattern Report",
            "",
            f"- Generated at: `{report['generated_at']}`",
            f"- Iteration: `{iteration}`",
            f"- Ads analysed: **{total}**",
            "",
            "## Winning patterns (model summary)",
        ]
        for wp in (report.get("winning_patterns") or [])[:5]:
            md_lines.append(
                f"- #{wp.get('rank')}: `{wp.get('hook_format')}` × `{wp.get('emotional_angle')}` — "
                f"**{float(wp.get('frequency_pct', 0.0))*100:.0f}%**"
            )
        md_lines += ["", "## Top combinations (computed)"]
        for row in report["top_combinations"]:
            md_lines.append(
                f"- `{row['hook_format']}` × `{row['emotional_angle']}` — "
                f"**{row['count']}** ads (**{row['frequency_pct']*100:.0f}%**)"
            )
            for ex in row["examples"]:
                md_lines.append(f"  - `{ex['brand']}` (`{ex['ad_id']}`): {ex['ad_copy_excerpt']}")
        md_lines += ["", "## Pattern gaps", *(f"- {g}" for g in (report.get("pattern_gaps") or []))]

        md_path = REPORTS_DIR / f"pattern_report_iter{iteration}_{ts}.md"
        md_path.write_text("\n".join(md_lines) + "\n")

        print(f"[Analyst] Exported pattern report → {json_path.name}, {md_path.name}")
    except Exception as e:
        print(f"[Analyst] Pattern report export warning: {e}")

    # Print pattern summary for real output visibility
    distinct_hooks = insights.get("distinct_hook_types_found", 0)
    print(f"[Analyst] Distinct hook types found: {distinct_hooks}")
    print(f"[Analyst] Total ads analysed: {insights.get('total_ads_analysed', len(validated_ads))}")

    for wp in insights.get("winning_patterns", []):
        print(
            f"  #{wp['rank']} {wp['hook_format']} × {wp['emotional_angle']} "
            f"({wp['frequency_pct']*100:.0f}%)"
        )

    # Determine if re-scrape is needed
    iteration = state.get("iteration_count", 1)
    rescrape = distinct_hooks < MIN_HOOK_TYPES and iteration < MAX_ITERATIONS

    if rescrape:
        print(
            f"[Analyst] Only {distinct_hooks} hook type(s) found (need {MIN_HOOK_TYPES}) "
            f"— signalling re-scrape (iteration {iteration}/{MAX_ITERATIONS})"
        )

    return {
        "extracted_insights": insights,
        "rescrape_needed": rescrape,
        "rescrape_keywords": _extract_rescrape_keywords(raw_ads) if rescrape else state.get("rescrape_keywords", []),
    }
