"""Developer utility: run one graph node in isolation and inspect its output.

Usage
-----
    python scripts/test_node.py <node>              # run node with default seed state
    python scripts/test_node.py <node> --from <f>   # seed state from a JSON file
    python scripts/test_node.py <node> --save <f>   # dump output state to JSON file

Available nodes
---------------
    load_context, generate_queries, search_jobs, search_companies,
    aggregate_jobs, analyze_jobs, store_results, send_notifications

Examples
--------
    # Test load_context (the very first node):
    python scripts/test_node.py load_context

    # Test search_jobs and save output for the next node:
    python scripts/test_node.py search_jobs --save /tmp/after_search.json

    # Test aggregate_jobs starting from saved state:
    python scripts/test_node.py aggregate_jobs --from /tmp/after_search.json

Each run prints:
  - Key fields produced by the node (jobs found, queries, CVs, etc.)
  - Any errors appended to state
  - New run_log entries added by this node
  - Optionally: raw diffs of every state key that changed
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is on sys.path regardless of where the script is invoked from.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Config loading (same merge logic as run.py) ───────────────────────────────

def _merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_config() -> dict:
    import yaml
    cfg: dict = {}
    for fname in ("config.yaml", "search_config.yaml", "score_config.yaml"):
        p = Path("config") / fname
        if p.exists():
            cfg = _merge(cfg, yaml.safe_load(p.read_text()) or {})
    return cfg


# ── Blank / seed state builders ───────────────────────────────────────────────
# Each node only needs certain fields pre-filled. The seed state provides
# the minimum required so the node doesn't crash on a missing key.

def _blank_state(cfg: dict) -> dict:
    return {
        "run_id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "config": cfg,
        "cvs": [],
        "raw_queries": [],
        "companies": [],
        "company_hints": {},
        "pdf_paths": [],
        "queries": [],
        "raw_jobs": [],
        "scored_jobs": [],
        "stored_count": 0,
        "sheet_url": None,
        "notification_sent": False,
        "errors": [],
        "run_log": [],
        "token_usage": {},
    }


# Nodes that need prior nodes to have run first. The script will auto-run
# prerequisites unless --from provides a checkpoint file.
_PREREQUISITES: dict[str, list[str]] = {
    "generate_queries":   ["load_context"],
    "search_jobs":        ["load_context", "generate_queries"],
    "search_companies":   ["load_context", "generate_queries"],
    "aggregate_jobs":     ["load_context", "generate_queries", "search_jobs", "search_companies"],
    "analyze_jobs":       ["load_context", "generate_queries", "search_jobs", "search_companies", "aggregate_jobs"],
    "store_results":      ["load_context", "generate_queries", "search_jobs", "search_companies", "aggregate_jobs", "analyze_jobs"],
    "send_notifications": ["load_context", "generate_queries", "search_jobs", "search_companies", "aggregate_jobs", "analyze_jobs", "store_results"],
}


def _get_node_fn(name: str):
    """Import and return the ``run`` function for the given node name."""
    import importlib
    mod = importlib.import_module(f"agent.nodes.{name}")
    return mod.run


# ── Output helpers ────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
DIM    = "\033[2m"


def _h(text: str, colour: str = BOLD) -> str:
    return f"{colour}{text}{RESET}"


def _print_section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 60}{RESET}")


def _fmt_list(items: list, max_items: int = 10) -> str:
    if not items:
        return f"  {DIM}(empty){RESET}"
    lines = [f"  · {item}" for item in items[:max_items]]
    if len(items) > max_items:
        lines.append(f"  {DIM}… and {len(items) - max_items} more{RESET}")
    return "\n".join(lines)


def _print_node_summary(node: str, before: dict, after: dict) -> None:
    """Print a human-readable summary of what the node produced."""
    _print_section(f"Node: {node}  [run_id={after.get('run_id')}]")

    # New errors
    new_errors = after.get("errors", [])[len(before.get("errors", [])):]
    if new_errors:
        print(f"\n{RED}Errors ({len(new_errors)}):{RESET}")
        for e in new_errors:
            print(f"  {RED}✗ {e}{RESET}")
    else:
        print(f"\n{GREEN}✓ No errors{RESET}")

    # New run_log entries
    new_log = after.get("run_log", [])[len(before.get("run_log", [])):]
    if new_log:
        print(f"\n{BOLD}Run log ({len(new_log)} new entries):{RESET}")
        for entry in new_log:
            print(f"  {DIM}»{RESET} {entry}")

    # Node-specific highlights
    _print_node_highlights(node, before, after)

    print()


def _print_node_highlights(node: str, before: dict, after: dict) -> None:
    """Print the key fields that this specific node is responsible for."""

    if node == "load_context":
        cvs = after.get("cvs", [])
        queries = after.get("raw_queries", [])
        companies = after.get("companies", [])
        hints = after.get("company_hints", {})
        pdfs = after.get("pdf_paths", [])

        print(f"\n{BOLD}CVs loaded ({len(cvs)}):{RESET}")
        for cv in cvs:
            chars = len(cv.get("content", ""))
            print(f"  · {cv['name']}  ({chars:,} chars)")

        if pdfs:
            print(f"\n{BOLD}PDFs queued ({len(pdfs)}):{RESET}")
            print(_fmt_list(pdfs))

        print(f"\n{BOLD}Queries from file ({len(queries)}):{RESET}")
        print(_fmt_list(queries))

        print(f"\n{BOLD}Companies ({len(companies)}):{RESET}")
        for c in companies:
            hint = hints.get(c, "—")
            print(f"  · {c}  {DIM}[hint: {hint}]{RESET}")

        print(f"\n{BOLD}Hints cache: {len(hints)} entries total{RESET}")

    elif node == "generate_queries":
        queries = after.get("queries", [])
        print(f"\n{BOLD}Generated queries ({len(queries)}):{RESET}")
        print(_fmt_list(queries))

    elif node in ("search_jobs", "search_companies", "aggregate_jobs"):
        before_jobs = len(before.get("raw_jobs", []))
        after_jobs  = len(after.get("raw_jobs", []))
        print(f"\n{BOLD}raw_jobs: {before_jobs} → {after_jobs}{RESET}")
        for job in after.get("raw_jobs", [])[:10]:
            src = job.get("source", "?")
            print(f"  · [{src}] {job.get('title', '?')} @ {job.get('company', '?')} — {job.get('location', '?')}")
            print(f"    {DIM}{job.get('url', '')[:80]}{RESET}")
        if after_jobs > 10:
            print(f"  {DIM}… and {after_jobs - 10} more{RESET}")

    elif node == "analyze_jobs":
        scored = after.get("scored_jobs", [])
        raw    = after.get("raw_jobs", [])
        print(f"\n{BOLD}Scored: {len(scored)}/{len(raw)} jobs passed threshold{RESET}")
        for job in sorted(scored, key=lambda j: j.get("score", 0), reverse=True)[:10]:
            rec = job.get("recommendation", "?")
            colour = GREEN if rec == "APPLY" else (YELLOW if rec == "CONSIDER" else DIM)
            print(f"  {colour}[{rec}] {job['score']:>3}  {job.get('title', '?')} @ {job.get('company', '?')}{RESET}")

    elif node == "store_results":
        print(f"\n{BOLD}Stored: {after.get('stored_count', 0)} jobs{RESET}")
        if after.get("sheet_url"):
            print(f"  Sheet: {after['sheet_url']}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Run one AJSAA graph node in isolation.")
    parser.add_argument("node", help="Node name (e.g. load_context, search_jobs)")
    parser.add_argument("--from", dest="from_file", metavar="FILE",
                        help="Load seed state from a JSON file (skip auto-prerequisites)")
    parser.add_argument("--save", dest="save_file", metavar="FILE",
                        help="Dump output state to a JSON file for chaining")
    parser.add_argument("--auto-prereqs", action="store_true",
                        help="Automatically run prerequisite nodes to build up state")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print full state diff for every changed key")
    args = parser.parse_args()

    node_name = args.node
    valid_nodes = list(_PREREQUISITES.keys()) + ["load_context"]
    if node_name not in valid_nodes:
        print(f"{RED}Unknown node '{node_name}'. Available: {', '.join(sorted(valid_nodes))}{RESET}")
        sys.exit(1)

    cfg = _load_config()
    state = _blank_state(cfg)

    # ── Seed state ────────────────────────────────────────────────────────────
    if args.from_file:
        print(f"{DIM}Loading seed state from {args.from_file}…{RESET}")
        with open(args.from_file, encoding="utf-8") as f:
            saved = json.load(f)
        # Merge saved state on top of blank (config always comes from disk)
        state = {**state, **saved, "config": cfg}

    elif args.auto_prereqs and node_name in _PREREQUISITES:
        prereqs = _PREREQUISITES[node_name]
        print(f"{DIM}Auto-running prerequisites: {' → '.join(prereqs)}{RESET}")
        for prereq in prereqs:
            fn = _get_node_fn(prereq)
            print(f"{DIM}  running {prereq}…{RESET}", end=" ", flush=True)
            state = fn(state)
            print(f"{DIM}done{RESET}")

    elif node_name in _PREREQUISITES:
        print(
            f"{YELLOW}Note: '{node_name}' normally needs {_PREREQUISITES[node_name]} to run first.\n"
            f"  Use --auto-prereqs to run them automatically, or --from <file> to load saved state.{RESET}\n"
        )

    # ── Run the node ──────────────────────────────────────────────────────────
    fn = _get_node_fn(node_name)
    before = dict(state)

    print(f"\n{BOLD}Running node: {node_name}{RESET}")
    print(f"{DIM}run_id={state['run_id']}  ts={state['timestamp']}{RESET}")

    result = fn(state)
    after = {**state, **result}

    _print_node_summary(node_name, before, after)

    # ── Verbose diff ──────────────────────────────────────────────────────────
    if args.verbose:
        _print_section("State diff (verbose)")
        for key in sorted(after.keys()):
            b_val = before.get(key)
            a_val = after.get(key)
            if b_val != a_val:
                b_repr = repr(b_val)[:120]
                a_repr = repr(a_val)[:120]
                print(f"  {BOLD}{key}{RESET}")
                print(f"    {RED}before: {b_repr}{RESET}")
                print(f"    {GREEN}after:  {a_repr}{RESET}")

    # ── Save output state ─────────────────────────────────────────────────────
    if args.save_file:
        # config is always reloaded from disk; don't persist it
        saveable = {k: v for k, v in after.items() if k != "config"}
        with open(args.save_file, "w", encoding="utf-8") as f:
            json.dump(saveable, f, indent=2, default=str)
        print(f"{GREEN}Output state saved to {args.save_file}{RESET}")

    if after.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
