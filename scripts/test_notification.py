#!/usr/bin/env python3
"""
Test notification channels without running the full pipeline.

Usage:
  # Test all channels configured in config.yaml:
  .venv/bin/python scripts/test_notification.py

  # Test specific channel(s):
  .venv/bin/python scripts/test_notification.py --channel telegram
  .venv/bin/python scripts/test_notification.py --channel email --channel slack

  # Use a specific jobs file:
  .venv/bin/python scripts/test_notification.py --jobs .data/jobs.json
"""
import argparse
import json
import sys
from pathlib import Path

# Run from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

_META_PATH = Path(".data/meta.json")


def _load_meta() -> dict:
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _load_state(jobs_path: str) -> dict:
    jobs_file = Path(jobs_path)
    scored_jobs = []
    if jobs_file.exists():
        raw = json.loads(jobs_file.read_text(encoding="utf-8"))
        scored_jobs = sorted(raw, key=lambda j: j.get("score", 0), reverse=True)

    from run import _load_config
    cfg = _load_config()
    meta = _load_meta()

    return {
        "config": cfg,
        "scored_jobs": scored_jobs,
        "stored_count": len([j for j in scored_jobs if j.get("status") == "new"]),
        "sheet_url": meta.get("sheet_url"),   # loaded from .data/meta.json after a real run
        "timestamp": meta.get("last_run", "test"),
        "errors": [],
        "run_log": [],
        "notification_sent": False,
    }


def _send(channel: str, state: dict) -> None:
    from typing import cast

    from agent.nodes.send_notifications import (
        _CHANNEL_FORMATTER,
        build_html_message,
        build_plain_message,
    )
    from agent.state import AgentState
    from providers.notifications.factory import build_notifier

    typed_state = cast(AgentState, state)
    formatter = _CHANNEL_FORMATTER.get(channel, build_plain_message)
    message = formatter(typed_state)
    html_body = build_html_message(typed_state) if channel == "email" else None

    notif_cfg = state["config"].get("notifications", {})
    notifier = build_notifier(channel, notif_cfg)
    notifier.send(message, html_body=html_body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test AJSAA notification channels")
    parser.add_argument("--channel", action="append", dest="channels",
                        help="Channel to test (email/slack/telegram/whatsapp). Repeatable.")
    parser.add_argument("--jobs", default=".data/jobs.json",
                        help="Path to jobs JSON file (default: .data/jobs.json)")
    args = parser.parse_args()

    state = _load_state(args.jobs)
    cfg_channels = state["config"].get("notifications", {}).get("channels", [])
    channels = args.channels or cfg_channels

    if not channels:
        print("No channels configured. Set notifications.channels in config.yaml or pass --channel.")
        sys.exit(1)

    print(f"Loaded {len(state['scored_jobs'])} jobs from {args.jobs}")
    if state.get("sheet_url"):
        print(f"Sheet URL: {state['sheet_url']}")
    else:
        print("Sheet URL: not set (run pipeline first to populate .data/meta.json)")
    print(f"Testing channels: {', '.join(channels)}\n")

    ok = True
    for ch in channels:
        try:
            _send(ch, state)
            print(f"  [OK]   {ch}")
        except Exception as e:
            print(f"  [FAIL] {ch}: {e}")
            ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
