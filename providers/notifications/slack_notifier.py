import json
import logging
import os
import urllib.request

from providers.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


def _require(var: str) -> str:
    raise ValueError(f"{var} not set — add it to .env (see .env.template)")


class SlackNotifier(BaseNotifier):
    def __init__(self, cfg: dict):
        self.webhook_url = os.environ.get("SLACK_WEBHOOK_URL") or _require("SLACK_WEBHOOK_URL")

    def send(self, message: str, html_body: str | None = None) -> None:
        payload = json.dumps({"text": message}).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Slack returned status {resp.status}")
        logger.info("Slack notification sent")
