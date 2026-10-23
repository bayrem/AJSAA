"""Developer utility: run the v3 spoke tools in a fixed order (no orchestrator yet).

Temporary stand-in until the M2 orchestrator exists. It exercises every tool
end-to-end against a real run store so the tools layer can be verified live:

    convert_cv (each CV in query/resume/) → web URL discovery → validate_urls
        → ats_search → score_batch

Usage
-----
    infisical run --env=dev -- python scripts/run_tools.py
    infisical run --env=dev -- python scripts/run_tools.py --skip-web   # ATS only, no web search tokens

Results land in ``.data/runs/<run_id>/``. Nothing is written to storage and
no notifications are sent. ``python run.py`` (the v2 LangGraph pipeline) is
unchanged and remains the production entry point until M2.
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

# Ensure the project root is on sys.path regardless of where the script is invoked from.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.run_store import CANDIDATES, RunStore  # noqa: E402
from agent.tools.cv import convert_cv  # noqa: E402
from agent.tools.scoring import score_batch  # noqa: E402
from agent.tools.search import ats_search, load_company_hints, validate_urls  # noqa: E402
from providers.llm import usage_tracker  # noqa: E402
from run import _load_config  # noqa: E402

RESUME_DIR = Path("query/resume")
# Same buffer as v2: ask for more URLs than we need, Tavily drops the bad ones.
WEB_URLS_REQUESTED = 80


def _positions(cfg: dict) -> list[str]:
    """Unique non-empty target titles from the ``cvs:`` block of search_config.yaml."""
    seen: list[str] = []
    for titles in cfg.get("cvs", {}).values():
        for title in titles or []:
            if title and title.strip() and title.strip() not in seen:
                seen.append(title.strip())
    return seen


def _discover_urls(cfg: dict, hints: dict[str, str]) -> list[dict]:
    """One web-search LLM call returning URL candidates (v2 directive prompt)."""
    from providers.llm.factory import build_llm
    from providers.search.web_search import AnthropicWebSearchProvider

    provider = AnthropicWebSearchProvider(build_llm(cfg["llm"], task="search"), cfg.get("search", {}))
    return provider.search_all(
        positions=_positions(cfg) or ["Product Manager"],
        locations=cfg.get("locations", ["Paris"]),
        companies=list(hints),
        hints=hints,
        max_results=WEB_URLS_REQUESTED,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the v3 spoke tools in a fixed order")
    parser.add_argument("--skip-web", action="store_true", help="Skip LLM web search (ATS boards only)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = _load_config()
    store = RunStore(str(uuid.uuid4())[:8])
    print(f"Run store: {store.dir}")

    def step(name: str, summary: str) -> None:
        print(f"\n── {name}\n{summary}")

    cv_paths = sorted(RESUME_DIR.glob("*.md")) + sorted(RESUME_DIR.glob("*.pdf"))
    if not cv_paths:
        sys.exit(f"No CVs found in {RESUME_DIR}/")
    for path in cv_paths:
        step("convert_cv", convert_cv(store, str(path)))

    hints = load_company_hints(cfg)
    if not args.skip_web:
        candidates = _discover_urls(cfg, hints)
        step("web discovery", f"{len(candidates)} URL candidates from web search")
        step("validate_urls", validate_urls(store, cfg.get("search", {}), candidates))
    step("ats_search", ats_search(store, cfg.get("search", {}), hints))
    if store.read_jsonl(CANDIDATES):
        step("score_batch", score_batch(store, cfg))
    else:
        step("score_batch", "skipped — no candidates found")

    total = usage_tracker.snapshot()["grand_total"]
    print(f"\nLLM calls: {total['calls']}  cost: ${total['cost_usd']:.4f}  billing: {cfg['llm'].get('billing', 'n/a')}")


if __name__ == "__main__":
    main()
