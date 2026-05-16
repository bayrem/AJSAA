"""Telegram notifier via the Bot API.

Sends a single message to a chat by ID. Optional ``parse_mode`` (e.g.
``Markdown`` or ``HTML``) can be set via ``config.yaml ->
notifications.telegram.parse_mode``; when unset the message is treated as
plain text.

Required environment variables (see ``.env.template``):
  - ``TELEGRAM_BOT_TOKEN`` — token from BotFather
  - ``TELEGRAM_CHAT_ID``   — numeric chat ID (use @userinfobot to find yours)
"""
import json
import logging
import urllib.request

from providers.notifications.base import BaseNotifier
from providers.utils import env_required

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Send the run digest as a single Telegram message."""

    def __init__(self, cfg: dict) -> None:
        self.token = env_required("TELEGRAM_BOT_TOKEN")
        self.chat_id = env_required("TELEGRAM_CHAT_ID")

        # parse_mode is optional: empty string → Telegram treats text as plain
        self.parse_mode = cfg.get("telegram", {}).get("parse_mode", "")

    def send(self, message: str, html_body: str | None = None) -> None:
        """Post ``message`` to the configured chat. ``html_body`` is ignored."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body: dict = {
            "chat_id": self.chat_id,
            "text": message,
            # Suppress the usual link-preview card — the message already contains
            # a labelled link.
            "disable_web_page_preview": True,
        }
        if self.parse_mode:
            body["parse_mode"] = self.parse_mode

        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data}")
        logger.info("Telegram notification sent to chat %s", self.chat_id)
