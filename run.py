"""AJSAA — main entry point."""
import argparse
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _setup_logging(cfg: dict) -> None:
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "logs/job_search.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def _load_config(path: str = "config.yaml") -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_initial_state(cfg: dict) -> dict:
    return {
        "run_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "config": cfg,
        "cvs": [],
        "raw_queries": [],
        "companies": [],
        "pdf_paths": [],
        "queries": [],
        "raw_jobs": [],
        "scored_jobs": [],
        "stored_count": 0,
        "sheet_url": None,
        "notification_sent": False,
        "errors": [],
        "run_log": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AJSAA — Autonomous Job Search AI Agent")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Score jobs without writing to storage")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    _setup_logging(cfg)
    logger = logging.getLogger("ajsaa")

    if args.dry_run:
        cfg["storage"]["provider"] = "local"
        logger.info("Dry-run mode — storage writes disabled")

    logger.info("=" * 60)
    logger.info("AJSAA run starting  [run_id=%s]", "dry-run" if args.dry_run else "live")

    from agent.graph import build_graph
    graph = build_graph()
    initial_state = _build_initial_state(cfg)

    final_state = graph.invoke(initial_state)

    logger.info("Run complete — %d new jobs stored", final_state.get("stored_count", 0))
    if final_state.get("errors"):
        for err in final_state["errors"]:
            logger.error("  ERROR: %s", err)
    logger.info("=" * 60)

    if final_state.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
