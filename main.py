"""
DITTO Meta Ads Automation — Streamlit Entry Point.

Run with:
    streamlit run main.py

The app drives the LangGraph workflow:
  1. Launches the graph (scraper → analyst → generator)
  2. Catches the interrupt() from the dashboard node
  3. Renders ad concepts for human review
  4. Resumes the graph with the reviewer's decision
"""

import json
import os
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from langgraph.types import Command

# Load `.env` first (production); fall back to `.env.example` for local dev.
_root = Path(__file__).resolve().parent
load_dotenv(_root / ".env")
load_dotenv(_root / ".env.example", override=False)

from graph.workflow import build_graph

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="DITTO Ad Automation",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — DITTO brand palette
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  :root {
    --ditto-green: #3B4A3F;
    --ditto-sage: #8A9E8C;
    --ditto-warm-white: #F7F5F0;
    --ditto-terracotta: #C4784A;
  }
  .concept-card {
    background: var(--ditto-warm-white);
    border-left: 4px solid var(--ditto-green);
    border-radius: 6px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.25rem;
  }
  .concept-id {
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--ditto-sage);
    text-transform: uppercase;
    margin-bottom: 0.4rem;
  }
  .hook-text {
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--ditto-green);
    margin-bottom: 0.5rem;
    line-height: 1.4;
  }
  .pattern-badge {
    display: inline-block;
    background: var(--ditto-green);
    color: white;
    font-size: 0.7rem;
    padding: 2px 8px;
    border-radius: 12px;
    margin-right: 4px;
    margin-bottom: 0.75rem;
  }
  .section-label {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--ditto-terracotta);
    text-transform: uppercase;
    margin-top: 0.75rem;
    margin-bottom: 0.2rem;
  }
  .confidence-bar-wrap { margin-top: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "graph" not in st.session_state:
    st.session_state.graph = build_graph()

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

if "phase" not in st.session_state:
    # Phases: "init" → "running" → "review" → "done"
    st.session_state.phase = "init"

if "interrupt_payload" not in st.session_state:
    st.session_state.interrupt_payload = None

if "run_log" not in st.session_state:
    st.session_state.run_log = []

def _competitors_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "competitors.json"


def get_config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def load_competitor_mapping() -> dict:
    return json.loads(_competitors_path().read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def render_concept_card(concept: dict, idx: int) -> None:
    confidence = concept.get("confidence_score", 0.0)
    confidence_pct = int(confidence * 100)

    with st.container():
        st.markdown(f"""
        <div class="concept-card">
          <div class="concept-id">{concept.get("concept_id", f"concept_{idx+1}")}</div>
          <div class="hook-text">{concept.get("hook", "—")}</div>
          <span class="pattern-badge">{concept.get("target_emotion", "")}</span>
          <span class="pattern-badge">{concept.get("cta", "")}</span>
        </div>
        """, unsafe_allow_html=True)

        col_copy, col_visual = st.columns([3, 2])

        with col_copy:
            st.markdown('<div class="section-label">Body Copy</div>', unsafe_allow_html=True)
            st.write(concept.get("body_copy", "—"))

            st.markdown('<div class="section-label">A/B Hypothesis</div>', unsafe_allow_html=True)
            st.caption(concept.get("a_b_test_hypothesis", "—"))

            st.markdown('<div class="section-label">Pattern Source</div>', unsafe_allow_html=True)
            st.caption(concept.get("pattern_source", "—"))

        with col_visual:
            st.markdown('<div class="section-label">Visual Prompt</div>', unsafe_allow_html=True)
            st.info(concept.get("visual_prompt", "—"))

            st.markdown('<div class="section-label">Confidence Score</div>', unsafe_allow_html=True)
            st.progress(confidence, text=f"{confidence_pct}% pattern match")

        st.divider()


def render_insights_sidebar(payload: dict) -> None:
    with st.sidebar:
        st.markdown("### Pattern Intelligence")
        st.caption(f"Analysed **{payload.get('total_ads_analysed', 0)}** long-running competitor ads")

        st.markdown("**Winning Patterns**")
        for wp in payload.get("winning_patterns", []):
            st.markdown(
                f"#{wp['rank']} `{wp['hook_format']}` × `{wp['emotional_angle']}` — "
                f"**{wp['frequency_pct']*100:.0f}%**"
            )

        st.divider()
        st.markdown("**Run Log**")
        for entry in st.session_state.run_log[-10:]:
            st.caption(entry)


def run_graph_until_interrupt(initial_input: dict | None = None, command: Command | None = None) -> bool:
    """
    Drive the graph forward. Returns True if we hit an interrupt (review needed),
    False if the graph reached END.
    """
    graph = st.session_state.graph
    config = get_config()

    invoke_arg = command if command else (initial_input or {})

    try:
        with st.spinner("Running DITTO Ad Automation pipeline..."):
            result = graph.invoke(invoke_arg, config=config)
    except Exception as e:
        st.error(f"Pipeline error: {e}")
        raise

    # Check for interrupt signal
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        st.session_state.interrupt_payload = payload
        st.session_state.phase = "review"
        st.session_state.run_log.append(
            f"⏸ Paused for review — {payload.get('total_concepts', 0)} concepts ready"
        )
        return True

    # Graph reached END
    st.session_state.phase = "done"
    return False


# ---------------------------------------------------------------------------
# UI Phases
# ---------------------------------------------------------------------------

def render_init_phase() -> None:
    st.title("DITTO Ad Automation")
    st.markdown(
        "This tool scrapes long-running competitor ads from the Meta Ad Library, "
        "extracts winning creative patterns, and generates DITTO-branded ad concepts "
        "ready for your review."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**What happens when you start:**")
        st.markdown("""
        1. 🔍 **Hunter** — pulls 60-day+ active ads from AG1, Huel, Ritual, Seed & more
        2. 🧠 **Analyst** — Gemini extracts hook formats, emotional angles, offer framing
        3. ✍️ **Creator** — generates 9 DITTO-branded concepts (3 per top pattern)
        4. 👁️ **You review** — approve, request a regeneration, or trigger a fresh scrape
        """)

    with col2:
        st.markdown("**Environment check:**")
        mock_meta = os.environ.get("USE_MOCK_META_ADS", "").strip().lower() in ("1", "true", "yes")
        meta_ok = bool(os.environ.get("META_ACCESS_TOKEN"))
        gemini_ok = bool(os.environ.get("GEMINI_API_KEY"))
        ads_ready = mock_meta or meta_ok
        st.markdown(
            f"{'✅' if ads_ready else '❌'} Competitor ads "
            f"(`META_ACCESS_TOKEN` **or** `USE_MOCK_META_ADS=true`)"
        )
        if mock_meta:
            st.caption("Mock mode on — `data/mock_ads.json` (no Meta API calls).")
        st.markdown(f"{'✅' if gemini_ok else '❌'} `GEMINI_API_KEY`")

        if not gemini_ok:
            st.warning("Set `GEMINI_API_KEY` in `.env`. See `.env.example`.")
        elif not ads_ready:
            st.warning(
                "Set `META_ACCESS_TOKEN` **or** enable mock mode: `USE_MOCK_META_ADS=true` in `.env`."
            )

    st.divider()

    if st.button("🚀 Start Pipeline", type="primary", disabled=not (gemini_ok and ads_ready)):
        st.session_state.phase = "running"
        st.session_state.run_log.append("▶ Pipeline started")
        st.rerun()


def render_running_phase() -> None:
    st.title("DITTO Ad Automation — Running")

    # Load default competitor page IDs from competitors.json
    import json as _json
    competitors = load_competitor_mapping()
    competitors.pop("_search_term_fallbacks", None)
    page_ids = list(competitors.values())

    initial_state = {
        "competitor_targets": page_ids,
        "raw_ads": [],
        "extracted_insights": {},
        "generated_concepts": [],
        "rescrape_needed": False,
        "rescrape_keywords": [],
        "approval_status": "pending",
        "human_feedback": None,
        "iteration_count": 0,
    }

    hit_interrupt = run_graph_until_interrupt(initial_input=initial_state)
    if not hit_interrupt and st.session_state.phase != "review":
        st.rerun()
    else:
        st.rerun()


def render_review_phase() -> None:
    payload = st.session_state.interrupt_payload or {}
    concepts = payload.get("concepts", [])
    iteration = payload.get("iteration", 1)
    scraped_total = payload.get("total_ads_scraped", 0)
    scraped_sample = payload.get("scraped_ads_sample", []) or []

    render_insights_sidebar(payload)

    st.title(f"DITTO Ad Concepts — Review (Iteration {iteration})")
    st.caption(
        f"{len(concepts)} concepts generated from "
        f"{payload.get('total_ads_analysed', 0)} competitor ads analysed."
    )

    st.divider()

    with st.expander(f"Scraper output (sample) — {scraped_total} ads scraped", expanded=False):
        st.caption(
            "This is the raw data returned by the scraper (sample only for UI). "
            "Each row includes the ad copy and snapshot URL so you can audit what fed the Analyst."
        )
        if not scraped_sample:
            st.info("No scraped ads found in payload.")
        else:
            rows = []
            for ad in scraped_sample:
                bodies = ad.get("ad_creative_bodies") or []
                copy = " | ".join(bodies) if bodies else ""
                # Show "long-running proof" via computed days running.
                days_running = ""
                start_raw = ad.get("ad_delivery_start_time") or ""
                try:
                    if start_raw:
                        from datetime import datetime as _dt
                        start_dt = _dt.strptime(start_raw, "%Y-%m-%d").date()
                        today_dt = _dt.now().date()
                        days_running = (today_dt - start_dt).days
                except Exception:
                    days_running = ""
                rows.append(
                    {
                        "ad_id": ad.get("id", ""),
                        "page_name": ad.get("page_name", ""),
                        "ad_delivery_start_time": ad.get("ad_delivery_start_time", ""),
                        "days_running": days_running,
                        "ad_snapshot_url": ad.get("ad_snapshot_url", ""),
                        "ad_copy": copy[:400] + ("…" if len(copy) > 400 else ""),
                    }
                )
            st.dataframe(rows, use_container_width=True)

    if not concepts:
        st.warning("No concepts were generated. Try re-scraping with different keywords.")
    else:
        for i, concept in enumerate(concepts):
            render_concept_card(concept, i)

    # Review controls
    st.markdown("### Your Decision")
    feedback = st.text_area(
        "Feedback for the AI (optional — describe what to improve or change)",
        placeholder="e.g. 'Make the hooks more direct. Less aspirational, more convenience-focused. Avoid the word journey.'",
        height=100,
    )

    col_approve, col_regen, col_rescrape = st.columns(3)

    with col_approve:
        if st.button("✅ Approve All Concepts", type="primary", use_container_width=True):
            _resume_graph("approve", feedback)

    with col_regen:
        if st.button("🔄 Reject — Regenerate Concepts", use_container_width=True):
            _resume_graph("rejected_regenerate", feedback)

    with col_rescrape:
        if st.button("🔍 Reject — Fresh Scrape", use_container_width=True):
            _resume_graph("rejected_rescrape", feedback)


def _resume_graph(action: str, feedback: str) -> None:
    st.session_state.run_log.append(f"→ Human decision: {action}")
    st.session_state.phase = "running_resume"
    st.session_state.pending_resume = Command(
        resume={"action": action, "feedback": feedback.strip() or None}
    )
    st.rerun()


def render_resume_phase() -> None:
    command = st.session_state.get("pending_resume")
    if not command:
        st.session_state.phase = "init"
        st.rerun()
        return

    st.title("DITTO Ad Automation — Resuming")
    hit_interrupt = run_graph_until_interrupt(command=command)
    st.session_state.pending_resume = None

    if not hit_interrupt:
        st.rerun()
    else:
        st.rerun()


def render_done_phase() -> None:
    render_insights_sidebar(st.session_state.interrupt_payload or {})

    st.title("✅ DITTO Ad Concepts Approved")
    st.success("Concepts have been approved and saved to `output/concepts/`.")

    concepts_dir = Path(__file__).parent / "output" / "concepts"
    files = sorted(concepts_dir.glob("*.json"), reverse=True)

    if files:
        latest = files[0]
        with open(latest) as f:
            approved = json.load(f)

        st.markdown(f"**Latest export:** `{latest.name}` ({len(approved)} concepts)")
        st.json(approved)

        st.download_button(
            label="⬇️ Download Concepts JSON",
            data=latest.read_text(),
            file_name=latest.name,
            mime="application/json",
        )

    st.divider()
    if st.button("🔁 Start New Run"):
        st.session_state.phase = "init"
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.interrupt_payload = None
        st.session_state.run_log = []
        st.rerun()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

phase = st.session_state.phase

if phase == "init":
    render_init_phase()
elif phase == "running":
    render_running_phase()
elif phase == "running_resume":
    render_resume_phase()
elif phase == "review":
    render_review_phase()
elif phase == "done":
    render_done_phase()
else:
    st.session_state.phase = "init"
    st.rerun()
