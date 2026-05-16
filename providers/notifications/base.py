"""Base interface for notification channels.

Every notifier implements ``send(message, html_body)``. Channels that don't
support HTML (Slack, Telegram, WhatsApp) ignore the ``html_body`` argument.
"""
from abc import ABC, abstractmethod


class BaseNotifier(ABC):
    """Abstract contract for notification channels."""

    @abstractmethod
    def send(self, message: str, html_body: str | None = None) -> None:
        """Send the run digest.

        Args:
            message: Plain-text version. Always provided. Required.
            html_body: Optional rich version. Used by ``email`` only; other
                channels should ignore it.
        """
