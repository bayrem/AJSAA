"""Search career pages for companies listed in ``config/search_config.yaml``.

Each company is routed via a hint stored in ``query/hints_cache.json``:

  - ``"greenhouse:<slug>"`` / ``"lever:<slug>"`` / ``"ashby:<slug>"``
        → call the ATS public API directly (no LLM, no scraping).
  - ``"url:https://..."``
        → run a focused web search prompted to look only at that URL.
  - ``"none"``
        → previous discovery failed; skip this company entirely.
  - missing key
        → call the LLM once to discover the career page URL, persist the
          result so we never pay for that discovery again.

Hash-based skip:
  A ``_companies_hash`` key is stored alongside hints in ``hints_cache.json``.
  When the hash of the current companies block matches the stored hash, we skip
  LLM discovery for any company that already has a cached hint. User-provided
  hints (from YAML) always take priority over both the cache and this skip.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agent.state import AgentState
from providers.utils import JsonCache

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

HINTS_CACHE_FILE = Path("query/hints_cache.json")
_HINTS_CACHE = JsonCache(HINTS_CACHE_FILE)

# Keywords used to filter ATS results to the Paris / France / remote scope.
# Anything not matching one of these is discarded by the connector.
_LOCATION_KEYWORDS = ["paris", "france", "remote", "télétravail", "hybrid", "île-de-france"]

DISCOVER_PROMPT = """What is the careers/jobs page URL for {company}?
Return only the URL (e.g. https://jobs.example.com), nothing else.
If you are not confident, return the word UNKNOWN."""

MAX_RESULTS_PER_COMPANY = 5

SEARCH_PROMPT = """Today is {today}. Search {scope} for open job positions at {company}.
Only include jobs posted in the last {recency_days} days.
Focus on roles matching these profiles: {cv_titles}
Return at most {max_results} results.

Return a JSON array where each item has:
- title: job title
- company: company name
- location: city / country
- url: direct link to the posting
- description: 2-3 sentence summary
- posted_date: date posted as YYYY-MM-DD (omit if unknown)

Return [] if no relevant positions found. Return only the JSON array, nothing else."""


def _compute_companies_hash(companies: list[str]) -> str:
    """Return a short SHA-256 digest of the sorted company names list.

    Sorting before hashing means order changes in the YAML don't invalidate
    the cache, which is almost always the right behaviour.
    """
    payload = json.dumps(sorted(companies), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _load_raw_cache() -> dict:
    """Load the full hints cache dict, including underscore-prefixed metadata keys."""
    raw = _HINTS_CACHE.load()
    if not isinstance(raw, dict):
        return {}
    return raw


def _save_hint(company: str, hint: str, companies_hash: str) -> None:
    """Persist a single hint and keep ``_companies_hash`` up to date."""
    raw = _load_raw_cache()
    # Preserve existing metadata keys; update company entry and hash.
    raw[company] = hint
    raw["_companies_hash"] = companies_hash
    _HINTS_CACHE.save(raw)


def _update_companies_hash(companies_hash: str) -> None:
    """Write the current hash to the cache without touching any hint entries."""
    raw = _load_raw_cache()
    raw["_companies_hash"] = companies_hash
    _HINTS_CACHE.save(raw)


def _discover_url(company: str, llm) -> str:
    """Ask the LLM for the company's career page URL. Returns 'none' on failure."""
    try:
        from langchain_core.messages import HumanMessage
        response = llm.invoke([HumanMessage(content=DISCOVER_PROMPT.format(company=company))])
        url = response.content.strip().rstrip(".")
        if url.startswith("http") and "." in url:
            return f"url:{url}"
    except Exception as e:
        logger.warning("URL discovery failed for '%s': %s", company, e)
    return "none"


def _ats_connector(ats_name: str, cfg: dict):
    """Return the appropriate ATS connector instance, or ``None`` if unknown.

    Delegates to :func:`providers.search.connectors.ats.build_ats_connector`
    — the three previously separate connector classes (Greenhouse, Lever,
    Ashby) now share one implementation parametrised by an ``AtsSpec``.
    """
    from providers.search.connectors.ats import build_ats_connector
    return build_ats_connector(ats_name, cfg)


def _search_with_hint(company: str, hint: str, llm, cfg: dict, cv_titles: str) -> list[dict]:
    """Execute search for a company given its routing hint."""
    if hint.startswith(("greenhouse:", "lever:", "ashby:")):
        ats_name, slug = hint.split(":", 1)
        connector = _ats_connector(ats_name, cfg.get("search", {}))
        if connector is None:
            return []
        results = connector.fetch(slug, location_keywords=_LOCATION_KEYWORDS)
        for job in results:
            job.setdefault("company", company)
        return results

    if not hint.startswith("url:"):
        return []

    from providers.search.web_search import AnthropicWebSearchProvider

    scope = hint[4:]
    recency_days = cfg.get("search", {}).get("recency_days", 3)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    provider = AnthropicWebSearchProvider(llm, cfg.get("search", {}))
    prompt = SEARCH_PROMPT.format(
        today=today,
        scope=scope,
        company=company,
        recency_days=recency_days,
        cv_titles=cv_titles,
        max_results=MAX_RESULTS_PER_COMPANY,
    )
    results = provider.search_with_prompt(prompt, max_results=MAX_RESULTS_PER_COMPANY)
    for job in results:
        job.setdefault("company", company)
    return results


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))
    raw_jobs = list(state.get("raw_jobs", []))

    companies = state.get("companies", [])
    if not companies:
        run_log.append("No companies in list — skipping company search")
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    if not state["config"].get("search", {}).get("enable_company_pages", True):
        run_log.append("Company page search disabled in config")
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    cfg = state["config"]
    cvs = state.get("cvs", [])
    # company_hints already has user-provided hints merged in (done by load_context).
    hints: dict = state.get("company_hints", {})
    cv_titles = ", ".join(cv["name"].replace("_", " ") for cv in cvs) or "product management, AI, data"

    # ── Hash-based skip: if the companies list hasn't changed, skip LLM
    #    discovery for companies that already have a cached hint. User-provided
    #    hints (already in `hints`) always take priority regardless.
    current_hash = _compute_companies_hash(companies)
    raw_cache = _load_raw_cache()
    cached_hash = raw_cache.get("_companies_hash", "")
    hash_matches = (current_hash == cached_hash)

    if hash_matches:
        run_log.append(f"[companies] Cache hash match ({current_hash[:8]}…) — skipping discovery for cached companies")
    else:
        run_log.append("[companies] Cache hash changed — will re-check uncached companies")

    try:
        from providers.llm.factory import build_llm
        llm = build_llm(cfg["llm"], task="search")
    except Exception as e:
        errors.append(f"Company search initialisation failed: {e}")
        logger.error("Company search init failed: %s", e)
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    for company in companies:
        try:
            hint = hints.get(company)

            if hint is None:
                # Only invoke LLM if the hash changed or this company was never cached.
                cached_entry = raw_cache.get(company)
                if hash_matches and cached_entry is not None:
                    hint = cached_entry
                    hints[company] = hint
                    run_log.append(f"[companies] '{company}' hint from cache (hash match): {hint}")
                else:
                    logger.info("[companies] '%s' — no hint, discovering career page...", company)
                    hint = _discover_url(company, llm)
                    _save_hint(company, hint, current_hash)
                    hints[company] = hint
                    run_log.append(f"[companies] '{company}' discovered hint: {hint}")
            else:
                run_log.append(f"[companies] '{company}' using hint: {hint}")

            if hint == "none":
                run_log.append(f"[companies] '{company}' skipped (hint=none)")
                logger.info("[companies] '%s' skipped (hint=none)", company)
                continue

            results = _search_with_hint(company, hint, llm, cfg, cv_titles)
            results = results[:MAX_RESULTS_PER_COMPANY]
            raw_jobs.extend(results)
            run_log.append(f"[companies] '{company}' → {len(results)} results")
            logger.info("[companies] '%s' → %d results", company, len(results))

        except Exception as e:
            errors.append(f"Company search failed for '{company}': {e}")
            logger.error("Company search failed for '%s': %s", company, e)

    # Persist the updated hash so the next run can skip LLM discovery.
    _update_companies_hash(current_hash)

    from providers.search.dedup import semantic_deduplicate
    before = len(raw_jobs)
    raw_jobs = semantic_deduplicate(raw_jobs)
    removed = before - len(raw_jobs)
    if removed:
        run_log.append(f"[companies] Semantic dedup removed {removed} near-duplicate jobs")
        logger.info("[companies] Semantic dedup removed %d near-duplicates", removed)

    return {**state, "raw_jobs": raw_jobs, "company_hints": hints, "errors": errors, "run_log": run_log}
