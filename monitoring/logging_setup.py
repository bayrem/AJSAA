"""Run-time logging configuration."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(cfg: dict, run_id: str) -> None:
    """Configure stdout + file logging from config.yaml's ``logging`` block.

    Rotation modes:
      - ``none``    — single growing file (default).
      - ``daily``   — rotate at midnight, keep ``retention`` backups.
      - ``per_run`` — fresh file per run, retain last ``retention``.
    """
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "logs/job_search.log")
    rotation = log_cfg.get("rotation", "none")
    retention = int(log_cfg.get("retention", 7))

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    if rotation == "daily":
        file_handler: logging.Handler = logging.handlers.TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=retention, encoding="utf-8"
        )
    elif rotation == "per_run":
        runs_dir = Path("logs/runs")
        runs_dir.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_log_path = runs_dir / f"run_{ts_str}.log"
        file_handler = logging.FileHandler(run_log_path, encoding="utf-8")
        all_logs = sorted(runs_dir.glob("run_*.log"), key=lambda p: p.stat().st_mtime)
        for old in all_logs[:-retention]:
            old.unlink(missing_ok=True)
    else:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), file_handler],
    )
