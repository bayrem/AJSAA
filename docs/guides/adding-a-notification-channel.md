# Adding a Notification Channel

Notifiers implement a single-method abstract interface.

## The interface

```python
# providers/notifications/base.py
class BaseNotifier(ABC):

    @abstractmethod
    def send(self, message: str, html_body: str | None = None) -> None:
        """Send a notification. html_body is the rich HTML version (email only)."""
```

## Example: Discord webhook

### 1. Implement the notifier

```python
# providers/notifications/discord_notifier.py

import json
import os
import urllib.request

from providers.notifications.base import BaseNotifier


class DiscordNotifier(BaseNotifier):
    def __init__(self, cfg: dict):
        self.webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    def send(self, message: str, html_body: str | None = None) -> None:
        payload = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
```

### 2. Register in the factory

```python
# providers/notifications/factory.py
elif channel == "discord":
    from providers.notifications.discord_notifier import DiscordNotifier
    return DiscordNotifier(cfg)
```

### 3. Add a message formatter

In `agent/nodes/send_notifications.py`, add a function and register it in `_CHANNEL_FORMATTER`:

```python
def build_discord_message(state: AgentState) -> str:
    count     = len(state.get("scored_jobs", []))
    sheet_url = state.get("sheet_url", "")
    ts        = state.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d")
    top       = state.get("scored_jobs", [])[:3]
    lines     = [f"**AJSAA** — {count} new matches on {ts}"]
    for j in top:
        lines.append(f"**{j['score']}** {j['title']} @ {j['company']}")
    if sheet_url:
        lines.append(sheet_url)
    return "\n".join(lines)


_CHANNEL_FORMATTER = {
    "email":    build_plain_message,
    "slack":    build_slack_message,
    "telegram": build_telegram_message,
    "whatsapp": build_plain_message,
    "discord":  build_discord_message,   # add this line
}
```

### 4. Configure

```yaml
# config.yaml
notifications:
  enabled: true
  channels: [telegram, discord]
```

```bash
# .env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## Notes

- `html_body` is only used by the email channel. Ignore it in all other notifiers.
- Each channel in the list is attempted independently — a failure in one does not block the others.
- The formatter receives the full `AgentState`, so you have access to all scored jobs, errors, and metadata.
