"""
Creator Node — "The Generator".

Takes winning patterns from the Analyst and produces DITTO-branded ad
concepts. Generates 3 variants per top winning pattern.
"""

import json
import os
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from graph.state import AgentState

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
CONCEPTS_DIR = Path(__file__).parent.parent.parent / "output" / "concepts"
CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
DITTO_ASSETS_DIR = Path(__file__).parent.parent.parent / "data" / "ditto_assets"

# Generate 3 variants × top N patterns
VARIANTS_PER_PATTERN = 3
TOP_PATTERNS = 3

# Forces the API to emit valid JSON (avoids broken strings from raw quotes in copy).
_AD_CONCEPT_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "concept_id": {"type": "string"},
        "pattern_source": {"type": "string"},
        "hook": {"type": "string"},
        "body_copy": {"type": "string"},
        "visual_prompt": {"type": "string"},
        "cta": {"type": "string"},
        "target_emotion": {"type": "string"},
        "a_b_test_hypothesis": {"type": "string"},
        "confidence_score": {"type": "number"},
    },
    "required": [
        "concept_id",
        "pattern_source",
        "hook",
        "body_copy",
        "visual_prompt",
        "cta",
        "target_emotion",
        "a_b_test_hypothesis",
        "confidence_score",
    ],
}

_AD_CONCEPT_LIST_SCHEMA: dict = {
    "type": "array",
    "items": _AD_CONCEPT_ITEM_SCHEMA,
}


def _load_system_prompt() -> str:
    return (PROMPTS_DIR / "generator_system.txt").read_text()


def _build_user_message(insights: dict, human_feedback: str | None, iteration: int) -> str:
    winning = insights.get("winning_patterns", [])
    gaps = insights.get("pattern_gaps", [])
    total = insights.get("total_ads_analysed", 0)

    lines = [
        f"I have analysed {total} long-running competitor ads and identified the following winning patterns.\n",
        f"Generate {VARIANTS_PER_PATTERN} DITTO ad concept variants for each of the top {TOP_PATTERNS} patterns below.\n",
    ]

    lines.append("WINNING PATTERNS (ranked by frequency):")
    for wp in winning[:TOP_PATTERNS]:
        lines.append(
            f"  #{wp['rank']}: hook_format={wp['hook_format']}, "
            f"emotional_angle={wp['emotional_angle']}, "
            f"frequency={wp['frequency_pct']*100:.0f}% of competitor ads"
        )

    if gaps:
        lines.append(f"\nPATTERN GAPS (under-used by competitors — consider as differentiators):")
        for gap in gaps:
            lines.append(f"  - {gap}")

    lines.append(f"\nThis is generation iteration #{iteration}.")

    if human_feedback:
        lines.append(
            f"\nHUMAN REVIEWER FEEDBACK from previous batch (apply this to improve concepts):\n"
            f"{human_feedback}"
        )

    lines.append(
        f"\nRemember: generate {VARIANTS_PER_PATTERN} variants × {min(TOP_PATTERNS, len(winning))} patterns "
        f"= {VARIANTS_PER_PATTERN * min(TOP_PATTERNS, len(winning))} total concept objects in your JSON array."
    )

    return "\n".join(lines)


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return raw


def _load_ditto_asset_image_parts() -> list[genai_types.Part]:
    """
    Optional brand grounding via reference images.

    If you place product/brand images in `data/ditto_assets/` (png/jpg/webp),
    we attach them to the Gemini request so the model can align visual prompts
    and copy more closely to real packaging.
    """
    if not DITTO_ASSETS_DIR.is_dir():
        return []

    parts: list[genai_types.Part] = []
    for p in sorted(DITTO_ASSETS_DIR.glob("*")):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }[p.suffix.lower()]
        try:
            parts.append(genai_types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))
        except Exception:
            continue
    return parts


def _call_gemini(system_prompt: str, user_message: str) -> list[dict]:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set in environment.")

    client = genai.Client(api_key=api_key)

    image_parts = _load_ditto_asset_image_parts()
    contents: list = [user_message]
    if image_parts:
        # A short hint so the model knows why images are present
        contents = [
            user_message
            + "\n\nReference images attached: use them to align DITTO packaging, tone, and visual prompts."
        ]
        contents.extend(image_parts)

    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL_GENERATOR", "gemini-3-flash-preview"),
        contents=contents,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_json_schema=_AD_CONCEPT_LIST_SCHEMA,
            temperature=0.85,   # higher temp for creative variation
        ),
    )

    raw = _strip_json_fences((response.text or "").strip())
    if not raw:
        raise ValueError("Empty JSON from Gemini (generator).")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        snippet = raw[max(0, e.pos - 120) : e.pos + 120]
        raise ValueError(f"Invalid JSON from Gemini: {e}\n--- snippet ---\n{snippet}") from e

    # Handle both a bare array and a wrapper object
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and "concepts" in parsed:
        return parsed["concepts"]
    return parsed


def _save_concepts(concepts: list[dict], iteration: int) -> None:
    """Persist concepts to disk for audit trail."""
    import time
    timestamp = int(time.time())
    path = CONCEPTS_DIR / f"concepts_iter{iteration}_{timestamp}.json"
    path.write_text(json.dumps(concepts, indent=2))
    print(f"[Generator] Concepts saved → {path}")


def generator_node(state: AgentState) -> dict:
    """
    LangGraph node: Creator.

    Produces DITTO-branded ad concepts informed by Analyst's winning patterns.
    Saves concepts to disk and returns them in state.
    """
    insights = state.get("extracted_insights", {})
    human_feedback = state.get("human_feedback")
    iteration = state.get("iteration_count", 1)

    if not insights.get("winning_patterns"):
        print("[Generator] No winning patterns found — cannot generate concepts")
        return {"generated_concepts": [], "approval_status": "pending"}

    print(f"[Generator] Generating concepts for iteration {iteration}")
    if human_feedback:
        print(f"[Generator] Applying human feedback: {human_feedback[:100]}...")

    system_prompt = _load_system_prompt()
    user_message = _build_user_message(insights, human_feedback, iteration)

    concepts = _call_gemini(system_prompt, user_message)

    # Tag each concept with its iteration number
    for i, concept in enumerate(concepts):
        concept["iteration"] = iteration
        if "concept_id" not in concept or not concept["concept_id"]:
            concept["concept_id"] = f"DITTO_v{iteration}_concept_{i+1}"

    print(f"[Generator] {len(concepts)} concepts generated")
    for c in concepts:
        print(f"  [{c.get('concept_id', '?')}] hook: {c.get('hook', '')[:60]}...")

    _save_concepts(concepts, iteration)

    return {
        "generated_concepts": concepts,
        "approval_status": "pending",
        "human_feedback": None,  # clear previous feedback after applying it
    }
