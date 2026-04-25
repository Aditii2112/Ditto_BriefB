from typing import Optional
from typing_extensions import TypedDict


class AgentState(TypedDict):
    # Input: list of Facebook Page IDs for competitor brands
    competitor_targets: list[str]

    # Raw ad objects returned by the Meta Ad Library API
    raw_ads: list[dict]

    # Structured pattern taxonomy produced by the Analyst node
    # Keys are competitor brand names; values are lists of AdPattern dicts
    extracted_insights: dict

    # DITTO-branded ad concept JSON objects ready for human review
    generated_concepts: list[dict]

    # Signals the workflow to loop back to the Scraper with alternate keywords
    rescrape_needed: bool

    # Alternate search_terms used when page-ID scraping returns sparse results
    rescrape_keywords: list[str]

    # "pending" → "approved" | "rejected_regenerate" | "rejected_rescrape"
    approval_status: str

    # Free-text notes entered by the human reviewer in Streamlit
    human_feedback: Optional[str]

    # Guards against unbounded scrape-analyse cycles (max 3)
    iteration_count: int
