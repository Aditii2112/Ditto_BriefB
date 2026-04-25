"""
HITL Dashboard Node — "The Checkpoint".

Calls LangGraph's interrupt() to pause the graph and surface generated
concepts to the Streamlit UI for human review. Resumes with a Command
carrying the reviewer's decision: approve, regenerate, or rescrape.
"""

from langgraph.types import interrupt

from graph.state import AgentState


def dashboard_node(state: AgentState) -> dict:
    """
    LangGraph node: HITL Dashboard.

    Pauses the graph and surfaces concepts + insights to the Streamlit
    reviewer. The graph resumes when the human submits a decision.

    Possible resume values (dict from Streamlit):
      {"action": "approve",            "feedback": "..."}
      {"action": "rejected_regenerate", "feedback": "..."}
      {"action": "rejected_rescrape",   "feedback": "..."}
    """
    concepts = state.get("generated_concepts", [])
    insights = state.get("extracted_insights", {})
    raw_ads = state.get("raw_ads", [])
    iteration = state.get("iteration_count", 1)

    # This call pauses the graph. Streamlit catches the __interrupt__ payload,
    # renders the review UI, and resumes the graph with a Command(resume=...).
    human_decision: dict = interrupt({
        "iteration": iteration,
        "total_concepts": len(concepts),
        "concepts": concepts,
        "total_ads_scraped": len(raw_ads),
        # keep payload small; show a sample + export full set to disk separately
        "scraped_ads_sample": raw_ads[:25],
        "winning_patterns": insights.get("winning_patterns", []),
        "total_ads_analysed": insights.get("total_ads_analysed", 0),
        "prompt": (
            "Review the generated DITTO ad concepts above. "
            "Choose: Approve all | Reject & regenerate | Reject & re-scrape"
        ),
    })

    action = human_decision.get("action", "approve")
    feedback = human_decision.get("feedback", "")

    if action == "approve":
        return {
            "approval_status": "approved",
            "human_feedback": feedback or None,
        }

    if action == "rejected_regenerate":
        return {
            "approval_status": "rejected_regenerate",
            "human_feedback": feedback or None,
            # iteration_count stays the same; generator will re-use same insights
        }

    if action == "rejected_rescrape":
        return {
            "approval_status": "rejected_rescrape",
            "human_feedback": feedback or None,
            "rescrape_needed": True,
        }

    # Fallback: treat unknown action as approve
    return {"approval_status": "approved", "human_feedback": feedback or None}
