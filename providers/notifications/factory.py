"""Notification-channel factory.

Mirrors the storage and LLM factories — picks a concrete notifier class
based on the channel name. Called from :mod:`agent.nodes.send_notifications`
once per configured channel.
"""


def build_notifier(channel: str, cfg: dict):
    """Return a configured ``BaseNotifier`` for the given channel name.

    Args:
        channel: One of ``email``, ``slack``, ``telegram``, ``whatsapp``
            (case-insensitive).
        cfg: The full ``notifications`` config block — each notifier reads
            its own sub-key (e.g. ``cfg["email"]["subject"]``).

    Raises:
        ValueError: If ``channel`` is not recognised.
    """
    channel = channel.lower()
    if channel == "email":
        from providers.notifications.email_notifier import EmailNotifier
        return EmailNotifier(cfg)
    elif channel == "slack":
        from providers.notifications.slack_notifier import SlackNotifier
        return SlackNotifier(cfg)
    elif channel == "telegram":
        from providers.notifications.telegram_notifier import TelegramNotifier
        return TelegramNotifier(cfg)
    elif channel == "whatsapp":
        from providers.notifications.whatsapp_notifier import WhatsAppNotifier
        return WhatsAppNotifier(cfg)
    else:
        raise ValueError(
            f"Unknown notification channel: '{channel}'. "
            "Supported: email, slack, telegram, whatsapp"
        )
