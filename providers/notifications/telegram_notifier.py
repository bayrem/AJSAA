import json
import logging
import os
import urllib.request

from providers.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


def _require(var: str) -> str:
    raise ValueError(f"{var} not set — add it to .env (see .env.template)")


class TelegramNotifier(BaseNotifier):
    def __init__(self, cfg: dict):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN") or _require("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID") or _require("TELEGRAM_CHAT_ID")
        self.parse_mode = cfg.get("telegram", {}).get("parse_mode", "")

    def send(self, message: str, html_body: str | None = None) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body: dict = {"chat_id": self.chat_id, "text": message, "disable_web_page_preview": True}
        if self.parse_mode:
            body["parse_mode"] = self.parse_mode
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if not data.get("ok"):
                raise RuntimeError(f"Telegram error: {data}")
        logger.info("Telegram notification sent to chat %s", self.chat_id)
