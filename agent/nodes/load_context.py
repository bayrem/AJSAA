"""Load CVs, queries, and companies from the query/ directory."""
import logging
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)

QUERY_DIR = Path("query")
RESUME_DIR = QUERY_DIR / "resume"
QUERIES_FILE = QUERY_DIR / "job_queries.md"
COMPANIES_FILE = QUERY_DIR / "company_list.md"


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    cvs: list[dict] = []
    raw_queries: list[str] = []
    companies: list[str] = []
    pdf_paths: list[str] = []

    # Load MD CVs
    if RESUME_DIR.exists():
        for md_file in sorted(RESUME_DIR.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                cvs.append({"name": md_file.stem, "content": content, "path": str(md_file)})
                run_log.append(f"Loaded CV: {md_file.name}")
            except Exception as e:
                errors.append(f"Failed to load CV {md_file}: {e}")

        # Collect PDFs for conversion
        for pdf_file in sorted(RESUME_DIR.glob("*.pdf")):
            pdf_paths.append(str(pdf_file))
            run_log.append(f"Queued PDF for conversion: {pdf_file.name}")

    max_cvs = state["config"].get("scoring", {}).get("max_cvs", 5)
    if len(cvs) > max_cvs:
        cvs = cvs[:max_cvs]
        run_log.append(f"Truncated CVs to max_cvs={max_cvs}")

    if not cvs and not pdf_paths:
        errors.append("No CVs found in query/resume/. Add .md or .pdf files.")

    # Load queries
    if QUERIES_FILE.exists():
        lines = QUERIES_FILE.read_text(encoding="utf-8").splitlines()
        raw_queries = [
            line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]
        run_log.append(f"Loaded {len(raw_queries)} queries from {QUERIES_FILE}")
    else:
        run_log.append("No job_queries.md found — LLM will generate queries from CVs")

    # Load companies
    if COMPANIES_FILE.exists():
        lines = COMPANIES_FILE.read_text(encoding="utf-8").splitlines()
        companies = [
            line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]
        run_log.append(f"Loaded {len(companies)} companies from {COMPANIES_FILE}")

    logger.info("Context loaded: %d CVs, %d PDFs, %d queries, %d companies",
                len(cvs), len(pdf_paths), len(raw_queries), len(companies))

    return {
        **state,
        "cvs": cvs,
        "raw_queries": raw_queries,
        "companies": companies,
        "pdf_paths": pdf_paths,
        "errors": errors,
        "run_log": run_log,
    }
