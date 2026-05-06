"""WhatsApp notifier — placeholder (requires Twilio or similar)."""
import logging

from providers.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


class WhatsAppNotifier(BaseNotifier):
    """
    Placeholder. Implement using Twilio's WhatsApp API:
    https://www.twilio.com/docs/whatsapp/api

    Required env vars: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
                       TWILIO_FROM_WHATSAPP, TWILIO_TO_WHATSAPP
    Add `twilio` to requirements.txt.
    """

    def __init__(self, cfg: dict):
        logger.warning("WhatsAppNotifier is a placeholder — messages will not be sent")

    def send(self, message: str, html_body: str | None = None) -> None:
        logger.warning("WhatsApp not implemented — skipping notification")
