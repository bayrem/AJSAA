"""Shared state shape passed between LangGraph nodes.

Every node receives and returns an :class:`AgentState`. New fields should be
declared here so every node has a uniform contract; nodes never invent state
keys on the fly.
"""
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """The full pipeline state. All fields are present at every step.

    Conventions:
      - ``errors``  accumulates non-fatal errors as strings; a non-empty list
        causes the final exit code to be 1 but does not abort the pipeline.
      - ``run_log`` accumulates human-readable progress messages used by the
        after-action report.
    """

    # ── Identity / config ───────────────────────────────────────────────────
    run_id: str        # short UUID generated in run.py for this run
    timestamp: str     # human-readable UTC time string (for displays)
    config: dict       # the full parsed config.yaml

    # ── Input layer (populated by load_context) ─────────────────────────────
    cvs: list[dict]          # [{"name": str, "content": str, "path": str}]
    raw_queries: list[str]   # Lines from query/job_queries.md (before generation)
    companies: list[str]     # Lines from query/company_list.md
    company_hints: dict      # {company: hint} loaded from query/hints_cache.json
    pdf_paths: list[str]     # PDF files found in query/resume/ (converted by convert_cvs)

    # ── Query generation (populated by generate_queries) ────────────────────
    queries: list[str]       # Final list — either raw_queries or LLM-generated

    # ── Search results (populated by search_jobs + search_companies) ────────
    raw_jobs: list[dict]     # All jobs found before scoring

    # ── Analysis (populated by analyze_jobs) ────────────────────────────────
    scored_jobs: list[dict]  # Jobs that passed the scoring threshold

    # ── Output (populated by store_results and send_notifications) ──────────
    stored_count: int
    sheet_url: str | None
    notification_sent: bool

    # ── Audit (accumulated by every node) ───────────────────────────────────
    errors: list[str]
    run_log: list[str]

    # ── Observability (populated at run end from usage_tracker.snapshot) ────
    # Shape: {"by_model": {...}, "by_node": {...}, "grand_total": {...}}.
    # Empty dict until run.py writes the final snapshot.
    token_usage: dict
