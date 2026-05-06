def build_notifier(channel: str, cfg: dict):
    """Return a BaseNotifier for the given channel name."""
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
        raise ValueError(f"Unknown notification channel: '{channel}'. Supported: email, slack, telegram, whatsapp")
