"""WhatsApp notifier — placeholder pending Twilio integration.

This stub exists so ``channels: [whatsapp]`` in config.yaml does not error.
To enable real sending: install ``twilio``, fill in the four ``TWILIO_*``
env vars in ``.env``, then implement ``send`` against
https://www.twilio.com/docs/whatsapp/api.
"""
import logging

from providers.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


class WhatsAppNotifier(BaseNotifier):
    """No-op notifier. Logs a warning whenever ``send`` is called."""

    def __init__(self, cfg: dict) -> None:
        # Warning fires at construction time so misconfigured deployments
        # surface immediately rather than only when a run completes.
        logger.warning("WhatsAppNotifier is a placeholder — messages will not be sent")

    def send(self, message: str, html_body: str | None = None) -> None:
        logger.warning("WhatsApp not implemented — skipping notification")
