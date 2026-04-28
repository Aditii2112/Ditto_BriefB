## Ditto Brief B — Meta Ads Automation

This project implements **Brief B: Meta Ads Automation** as a cyclic workflow (LangGraph) that:
- **Scrapes** long-running competitor ads from the **Meta Ad Library API**
- **Extracts patterns** (hook format, offer framing, emotional angle, etc.) using **Gemini**
- **Generates DITTO-branded concepts** grounded in DITTO brand guidance and optional product images
- **Routes through a HITL review** (Streamlit) for approve / regenerate / re-scrape decisions

### System flow (LangGraph)

```mermaid
flowchart TD
    START([Start]) --> scraper
    scraper["Hunter Node\n(Meta Ad Library API)"] --> analyst
    analyst["Analyst Node\n(Gemini Pattern Extraction)"] --> check{Sufficient\ninsights?}
    check -->|"No"| scraper
    check -->|Yes| generator
    generator["Creator Node\n(Gemini + DITTO Brand Context)"] --> dashboard
    dashboard["HITL Dashboard Node\n(Streamlit + interrupt())"] --> approval{Human\nDecision}
    approval -->|Approved| endNode([Save & Export])
    approval -->|Rejected — regenerate| generator
    approval -->|Rejected — new scrape| scraper
```

### “Real output”
- **Raw scraped ads**: exported to `output/ads/` per run (JSON)
- **Pattern report**: exported to `output/reports/` (JSON + Markdown)
- **Generated concepts**: exported to `output/concepts/` (JSON)
- **A/B test plan**: exported to `output/ab_tests/` (JSON + Markdown)
- **Review UI**: Streamlit review screen (concept cards + scraper sample table)

### Setup

1) Install dependencies

```bash
pip3 install -r requirements.txt
```

2) Add environment variables

Copy `.env.example` → `.env` and set:
- `META_ACCESS_TOKEN` (Meta Ad Library API token with `ads_read`)
- `GEMINI_API_KEY`

Optional:
- `GEMINI_MODEL_ANALYST` / `GEMINI_MODEL_GENERATOR` (defaults are fine)

3) Configure competitors

Edit `data/competitors.json` and paste **Meta Ad Library advertiser page IDs** (numbers) for each competitor.

### Run

```bash
streamlit run main.py
```

Open the app at the local URL (Streamlit prints it in the terminal), then click **Start Pipeline**.

### How to read the A/B test plan

Each run exports an A/B plan (for example: `output/ab_tests/ab_test_plan_iter1_*.json`) with:
- `test_id`: unique ID for tracking that test cycle
- `control`: the baseline concept to beat
- `treatments`: challenger concepts tested against control
- `kpis`: primary and secondary success metrics (CTR primary; CVR/CPC/CPA secondary)
- `budget_split_pct`: recommended spend split across control and treatments
- `recommended_runtime_days`: suggested minimum run window before decisions
- `decision_rules`: clear scale / iterate / kill logic

Plain-English interpretation:
- **Scale**: when a treatment beats control on CTR and keeps CPA healthy
- **Iterate**: when signal is promising but not decisive
- **Kill**: when treatment clearly underperforms

Confidence concepts (alpha / beta), simplified:
- **Alpha (Type I error)**: chance of calling a winner when it is not actually better (false positive)
- **Beta (Type II error)**: chance of missing a real winner (false negative)
- **Power = 1 - beta**: chance of detecting a true winner if it exists

This project currently exports a **structured decision framework** for A/B operations; statistical significance checks (alpha/beta-powered stop rules) are the next production enhancement.

### Ground generation with DITTO product images (optional)

Put DITTO product/brand images in:
- `data/ditto_assets/`

If present, the Generator attaches them to Gemini so visual prompts and tone align more closely with real packaging.

### Evaluation Criteria Coverage

- **Platform thinking**: I explicitly handle the real Meta constraint that keyword search is noisy and rely on advertiser `search_page_ids` for deterministic scraping. We also expose scrape settings (`META_AD_ACTIVE_STATUS`, lookback, long-running threshold) so the system can trade strictness vs. volume based on business context.
- **Commercial judgement**: The workflow prioritizes extracting winning hooks/offers/emotional angles from ads with sustained runtime and converts those into testable DITTO variants. Output includes structured hypotheses per concept to connect creative choices to CTR/CVR and downstream LTV-oriented iteration.
- **Technical depth**: The implementation is production-aware: cyclic LangGraph orchestration, typed shared state, deterministic JSON-schema-constrained generation, robust scraper error handling, artifact exports (`output/ads`, `output/reports`, `output/concepts`), and human-in-the-loop approval before publish.
- **Communication**: The Streamlit review UI presents raw scraped evidence, pattern summaries, and generated concepts in one place, making trade-offs visible to non-technical stakeholders. The README documents setup, architecture, and outputs in plain language.
- **Initiative**: Beyond the brief baseline, the project adds automated pattern reports, long-running proof (`ad_delivery_start_time` + `days_running`), and optional image-grounded brand conditioning via `data/ditto_assets/` to improve brand-faithful concept generation.

