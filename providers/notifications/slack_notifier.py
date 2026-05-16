"""Slack notifier via incoming webhook.

Posts a single mrkdwn-formatted message to the channel configured in the
webhook. No interactive features — this is fire-and-forget.

Required environment variables (see ``.env.template``):
  - ``SLACK_WEBHOOK_URL`` — full incoming-webhook URL from Slack app config
"""
import json
import logging
import urllib.request

from providers.notifications.base import BaseNotifier
from providers.utils import env_required

logger = logging.getLogger(__name__)


class SlackNotifier(BaseNotifier):
    """Post the run digest as a single Slack message via incoming webhook."""

    def __init__(self, cfg: dict) -> None:
        # cfg currently unused — webhook URL fully determines destination
        self.webhook_url = env_required("SLACK_WEBHOOK_URL")

    def send(self, message: str, html_body: str | None = None) -> None:
        """Post ``message`` to Slack. ``html_body`` is ignored (Slack uses mrkdwn)."""
        payload = json.dumps({"text": message}).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Slack webhook returned status {resp.status}")
        logger.info("Slack notification sent")
