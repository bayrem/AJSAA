"""Load CVs, queries, companies, and hints from the ``query/`` directory.

This is the first node in the pipeline. It owns all filesystem I/O for the
user-managed inputs: every other node receives its data via the graph state
and never reads from disk directly (except for caches and outputs).

Behaviour notes:
  - MD CVs are loaded directly. PDF CVs are queued for the convert_cvs node
    rather than parsed here, so this node stays IO-light.
  - When ``hints_cache.json`` is missing we bootstrap from
    ``hints_cache.example.json`` so first-run users get useful defaults.
  - Companies are read from ``config.companies`` (config/search_config.yaml).
    Three shapes are supported:
      - plain string     → ``"Mistral AI"``
      - hint dict        → ``{name: "Hugging Face", hint: "greenhouse:huggingface"}``
      - url dict         → ``{name: "Criteo", url: "https://jobs.lever.co/criteo"}``
    User-provided hint/url entries are merged into ``company_hints`` at load
    time so ``search_companies`` can override the cache without extra logic.
"""
import json
import logging
import shutil
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)


# ── File paths ───────────────────────────────────────────────────────────────

QUERY_DIR = Path("query")
RESUME_DIR = QUERY_DIR / "resume"
QUERIES_FILE = QUERY_DIR / "job_queries.md"
HINTS_CACHE_FILE = QUERY_DIR / "hints_cache.json"
HINTS_EXAMPLE_FILE = QUERY_DIR / "hints_cache.example.json"


# ── Hints cache ──────────────────────────────────────────────────────────────

def _load_hints_cache() -> dict:
    """Load the hints cache, bootstrapping from the example file on first run.

    Underscore-prefixed keys (e.g. ``_comment``) in the example file are
    documentation annotations; we strip them so they never get treated as
    company names by downstream code.
    """
    if not HINTS_CACHE_FILE.exists():
        if HINTS_EXAMPLE_FILE.exists():
            shutil.copy(HINTS_EXAMPLE_FILE, HINTS_CACHE_FILE)
            logger.info("hints_cache.json created from example file")
        else:
            return {}
    try:
        raw = json.loads(HINTS_CACHE_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception as e:
        # Corrupt cache shouldn't break the run — start with no hints
        logger.warning("Failed to load hints_cache.json: %s", e)
        return {}


# ── Markdown-list helpers ────────────────────────────────────────────────────

def _read_md_list(path: Path) -> list[str]:
    """Read a markdown file as a list of non-blank, non-comment lines."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ── Companies parsing ────────────────────────────────────────────────────────

def _parse_companies(
    raw_companies: list,
) -> tuple[list[str], dict[str, str]]:
    """Parse the ``companies:`` block from config into a flat list + inline hints.

    Returns:
        (company_names, inline_hints) where ``inline_hints`` maps company name
        to the user-provided hint string. These inline hints have the highest
        priority and will override anything stored in ``hints_cache.json``.

    Supported shapes in the YAML list:
      - Plain string: ``"Mistral AI"``
          → name=``"Mistral AI"``, no inline hint
      - hint dict: ``{name: "Hugging Face", hint: "greenhouse:huggingface"}``
          → name=``"Hugging Face"``, inline_hint=``"greenhouse:huggingface"``
      - url dict: ``{name: "Criteo", url: "https://jobs.lever.co/criteo"}``
          → name=``"Criteo"``, inline_hint=``"url:https://jobs.lever.co/criteo"``
    """
    names: list[str] = []
    inline_hints: dict[str, str] = {}

    for entry in raw_companies:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("name", "").strip()
            if not name:
                logger.warning("Skipping company entry with missing 'name': %s", entry)
                continue
            names.append(name)
            if "hint" in entry:
                inline_hints[name] = entry["hint"]
            elif "url" in entry:
                url = entry["url"]
                inline_hints[name] = f"url:{url}" if not url.startswith("url:") else url
        else:
            logger.warning("Skipping unrecognised company entry type: %s", type(entry).__name__)

    return names, inline_hints


# ── Graph node ───────────────────────────────────────────────────────────────

def run(state: AgentState) -> AgentState:
    """Populate every input-layer field on the agent state."""
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    cvs: list[dict] = []
    raw_queries: list[str] = []
    companies: list[str] = []
    pdf_paths: list[str] = []

    # ── CVs (MD loaded immediately, PDF queued for the next node) ───────────
    if RESUME_DIR.exists():
        for md_file in sorted(RESUME_DIR.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                cvs.append({"name": md_file.stem, "content": content, "path": str(md_file)})
                run_log.append(f"Loaded CV: {md_file.name}")
            except Exception as e:
                errors.append(f"Failed to load CV {md_file}: {e}")

        for pdf_file in sorted(RESUME_DIR.glob("*.pdf")):
            # Don't parse PDFs here — that's convert_cvs's job.
            pdf_paths.append(str(pdf_file))
            run_log.append(f"Queued PDF for conversion: {pdf_file.name}")

    # Cap CVs at max_cvs to keep prompt sizes predictable
    max_cvs = state["config"].get("scoring", {}).get("max_cvs", 5)
    if len(cvs) > max_cvs:
        cvs = cvs[:max_cvs]
        run_log.append(f"Truncated CVs to max_cvs={max_cvs}")

    if not cvs and not pdf_paths:
        errors.append("No CVs found in query/resume/. Add .md or .pdf files.")

    # ── Queries ─────────────────────────────────────────────────────────────
    if QUERIES_FILE.exists():
        raw_queries = _read_md_list(QUERIES_FILE)
        run_log.append(f"Loaded {len(raw_queries)} queries from {QUERIES_FILE}")
    else:
        # Empty raw_queries triggers generate_queries to call the LLM
        run_log.append("No job_queries.md found — LLM will generate queries from CVs")

    # ── Companies (read from config, not from company_list.md) ──────────────
    raw_companies = state["config"].get("companies", [])
    if raw_companies:
        companies, inline_hints = _parse_companies(raw_companies)
        run_log.append(f"Loaded {len(companies)} companies from search_config.yaml")
    else:
        inline_hints = {}

    # ── Hints cache ─────────────────────────────────────────────────────────
    company_hints = _load_hints_cache()

    # User-provided hints (from YAML) always override the cache.
    company_hints.update(inline_hints)
    if inline_hints:
        run_log.append(
            f"Applied {len(inline_hints)} user-provided hints from config: {list(inline_hints.keys())}"
        )

    hint_count = sum(1 for c in companies if c in company_hints)
    run_log.append(f"Hints cache: {hint_count}/{len(companies)} companies have hints")
    logger.info(
        "Context loaded: %d CVs, %d PDFs, %d queries, %d companies (%d hinted)",
        len(cvs), len(pdf_paths), len(raw_queries), len(companies), hint_count,
    )

    return {
        **state,
        "cvs": cvs,
        "raw_queries": raw_queries,
        "companies": companies,
        "company_hints": company_hints,
        "pdf_paths": pdf_paths,
        "errors": errors,
        "run_log": run_log,
    }
