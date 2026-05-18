"""Generate job-search queries deterministically from search_config.yaml.

Produces the cross-product of (positions × locations) defined under the
``cvs:`` and ``locations:`` keys in search_config.yaml. Positions per CV are
capped at 2. Results are written to ``query/job_queries.md`` with a SHA-256
hash header so the file is only regenerated when search_config.yaml changes.

No LLM call is made. The LLM-based fallback has been removed entirely.
"""
import hashlib
import logging
from itertools import product
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)

_SEARCH_CONFIG_PATH = Path("config/search_config.yaml")
_QUERIES_FILE = Path("query/job_queries.md")

_HASH_PREFIX = "# hash: "
_MAX_POSITIONS_PER_CV = 2


def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cached_hash(queries_file: Path) -> str | None:
    """Return the hash written on line 1 of an existing queries file, or None."""
    if not queries_file.exists():
        return None
    first_line = queries_file.read_text(encoding="utf-8").splitlines()[0] if queries_file.stat().st_size else ""
    if first_line.startswith(_HASH_PREFIX):
        return first_line[len(_HASH_PREFIX):].strip()
    return None


def _build_queries(cvs_cfg: dict, locations: list[str]) -> list[str]:
    """Cross-product of (capped positions from all CVs) × locations."""
    positions: list[str] = []
    for cv_key in sorted(cvs_cfg):
        cv_positions = cvs_cfg[cv_key][:_MAX_POSITIONS_PER_CV]
        positions.extend(cv_positions)

    return [f"{pos} {loc}" for pos, loc in product(positions, locations)]


def _write_queries_file(path: Path, queries: list[str], config_hash: str) -> None:
    lines = [f"{_HASH_PREFIX}{config_hash}", ""]
    lines.extend(queries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    cvs_cfg: dict = state["config"].get("cvs", {})
    locations: list[str] = state["config"].get("locations", [])

    if not cvs_cfg:
        errors.append("generate_queries: no 'cvs' key in config — cannot build queries")
        return {**state, "queries": [], "errors": errors, "run_log": run_log}

    if not locations:
        errors.append("generate_queries: no 'locations' key in config — cannot build queries")
        return {**state, "queries": [], "errors": errors, "run_log": run_log}

    # If queries were already loaded from job_queries.md by load_context, honour
    # them only if the hash matches. Otherwise regenerate.
    current_hash = _sha256_of_file(_SEARCH_CONFIG_PATH) if _SEARCH_CONFIG_PATH.exists() else ""
    cached = _cached_hash(_QUERIES_FILE)

    if cached == current_hash and cached:
        queries = state.get("raw_queries", [])
        run_log.append(
            f"generate_queries: cache hit (hash {current_hash[:8]}…) — "
            f"using {len(queries)} queries from {_QUERIES_FILE}"
        )
        logger.info("Query cache hit — reusing %d queries", len(queries))
        return {**state, "queries": queries, "errors": errors, "run_log": run_log}

    queries = _build_queries(cvs_cfg, locations)
    _write_queries_file(_QUERIES_FILE, queries, current_hash)

    run_log.append(
        f"generate_queries: wrote {len(queries)} queries to {_QUERIES_FILE} "
        f"(hash {current_hash[:8]}…)"
    )
    logger.info("Generated %d queries → %s", len(queries), _QUERIES_FILE)

    return {**state, "queries": queries, "errors": errors, "run_log": run_log}
