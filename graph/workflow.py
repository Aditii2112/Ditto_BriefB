"""
LangGraph StateGraph definition.

Graph topology:
  START → scraper → analyst → [rescrape? → scraper | generator] → dashboard
                                                                      ↓
                                [approved → END | regenerate → generator | rescrape → scraper]
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.nodes.analyst import analyst_node
from graph.nodes.dashboard import dashboard_node
from graph.nodes.generator import generator_node
from graph.nodes.scraper import scraper_node
from graph.state import AgentState


# ---------------------------------------------------------------------------
# Conditional edge logic
# ---------------------------------------------------------------------------

def route_after_analyst(state: AgentState) -> str:
    """
    After analyst runs:
    - If re-scrape needed and we haven't hit the iteration ceiling → scraper
    - Otherwise → generator
    """
    if state.get("rescrape_needed") and state.get("iteration_count", 1) <= 3:
        return "scraper"
    return "generator"


def route_after_dashboard(state: AgentState) -> str:
    """
    After human review:
    - approved → END
    - rejected_regenerate → generator (reuse same insights, apply feedback)
    - rejected_rescrape → scraper (fetch fresh data)
    """
    status = state.get("approval_status", "approved")

    if status == "approved":
        return END

    if status == "rejected_regenerate":
        return "generator"

    if status == "rejected_rescrape":
        return "scraper"

    # Default fallback
    return END


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph():
    """Compile and return the LangGraph StateGraph with MemorySaver checkpointer."""
    builder = StateGraph(AgentState)

    # Register nodes
    builder.add_node("scraper", scraper_node)
    builder.add_node("analyst", analyst_node)
    builder.add_node("generator", generator_node)
    builder.add_node("dashboard", dashboard_node)

    # Entry point
    builder.add_edge(START, "scraper")

    # scraper → analyst (always)
    builder.add_edge("scraper", "analyst")

    # analyst → scraper (re-scrape) OR analyst → generator
    builder.add_conditional_edges(
        "analyst",
        route_after_analyst,
        {
            "scraper": "scraper",
            "generator": "generator",
        },
    )

    # generator → dashboard (always)
    builder.add_edge("generator", "dashboard")

    # dashboard → END | generator | scraper
    builder.add_conditional_edges(
        "dashboard",
        route_after_dashboard,
        {
            END: END,
            "generator": "generator",
            "scraper": "scraper",
        },
    )

    # MemorySaver enables interrupt() to persist state between pause and resume
    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    return graph
