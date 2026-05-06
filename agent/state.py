from typing import Optional

from typing_extensions import TypedDict


class AgentState(TypedDict):
    run_id: str
    timestamp: str
    config: dict

    # Input layer
    cvs: list[dict]          # [{"name": str, "content": str, "path": str}]
    raw_queries: list[str]   # Lines from query/job_queries.md
    companies: list[str]     # Lines from query/company_list.md
    pdf_paths: list[str]     # PDF files found in query/resume/

    # Generated
    queries: list[str]       # Final queries (from file or LLM-generated)

    # Search results
    raw_jobs: list[dict]     # All jobs found before scoring

    # Analysis
    scored_jobs: list[dict]  # Jobs that passed scoring threshold

    # Output
    stored_count: int
    sheet_url: Optional[str]
    notification_sent: bool

    # Audit
    errors: list[str]
    run_log: list[str]
