"""Send run summary via configured notification channels."""
import html as html_mod
import logging
from datetime import datetime

from agent.state import AgentState

logger = logging.getLogger(__name__)


def _top_jobs_lines(scored_jobs: list[dict], n: int = 5) -> list[dict]:
    return scored_jobs[:n]


def build_plain_message(state: AgentState) -> str:
    """Plain text digest — used by email (text part) and as fallback."""
    scored_jobs = state.get("scored_jobs", [])
    top_jobs = _top_jobs_lines(scored_jobs)
    errors = state.get("errors", [])
    ts = state.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d")

    lines = [
        f"AJSAA — Run Report ({ts})",
        f"New jobs stored : {state.get('stored_count', 0)}",
        f"Total scored    : {len(scored_jobs)}",
    ]

    if top_jobs:
        lines.append("\nTop Matches:")
        for j in top_jobs:
            rec = j.get("recommendation", "")
            tag = f"[{j.get('score')}{'/' + rec if rec else ''}]"
            lines.append(
                f"  {tag} {j.get('title')} @ {j.get('company')} — {j.get('location')}"
            )
            if j.get("url"):
                lines.append(f"       {j['url']}")

    if state.get("sheet_url"):
        lines.append(f"\nGoogle Sheet: {state['sheet_url']}")

    if errors:
        lines.append(f"\n[!] {len(errors)} error(s) — check logs/job_search.log")

    return "\n".join(lines)


def build_html_message(state: AgentState) -> str:
    """HTML digest — used as the rich part of email."""
    scored_jobs = state.get("scored_jobs", [])
    top_jobs = _top_jobs_lines(scored_jobs)
    errors = state.get("errors", [])
    ts = state.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d")

    row_parts: list[str] = []
    for j in top_jobs:
        raw_url = j.get("url", "")
        safe_url = html_mod.escape(raw_url) if raw_url.startswith(("https://", "http://")) else "#"
        title = html_mod.escape(j.get("title", ""))
        title_link = f'<a href="{safe_url}">{title}</a>' if safe_url != "#" else title
        badge_color = "#2e7d32" if j.get("score", 0) >= 80 else "#f57c00"
        row_parts.append(
            f"<tr>"
            f'<td style="padding:6px 10px;"><span style="background:{badge_color};color:#fff;border-radius:4px;padding:2px 6px;font-size:13px;">{j.get("score")}</span></td>'
            f'<td style="padding:6px 10px;">{title_link}</td>'
            f'<td style="padding:6px 10px;">{html_mod.escape(j.get("company",""))}</td>'
            f'<td style="padding:6px 10px;">{html_mod.escape(j.get("recommendation",""))}</td>'
            f"</tr>"
        )
    rows = "".join(row_parts)

    sheet_link = ""
    raw_sheet_url = state.get("sheet_url", "")
    if raw_sheet_url and raw_sheet_url.startswith(("https://", "http://")):
        escaped_sheet_url = html_mod.escape(raw_sheet_url)
        sheet_link = f'<p><a href="{escaped_sheet_url}">Open Google Sheet →</a></p>'
    elif raw_sheet_url:
        sheet_link = f'<p>Google Sheet: {html_mod.escape(raw_sheet_url)}</p>'

    error_block = ""
    if errors:
        error_block = f'<p style="color:#c62828;">⚠ {len(errors)} error(s) — check logs/job_search.log</p>'

    return f"""<html><body style="font-family:sans-serif;color:#222;">
<h2 style="margin-bottom:4px;">AJSAA — Run Report</h2>
<p style="color:#666;margin-top:0;">{ts}</p>
<table style="border-collapse:collapse;width:100%;max-width:700px;">
  <thead>
    <tr style="background:#f5f5f5;">
      <th style="padding:6px 10px;text-align:left;">Score</th>
      <th style="padding:6px 10px;text-align:left;">Title</th>
      <th style="padding:6px 10px;text-align:left;">Company</th>
      <th style="padding:6px 10px;text-align:left;">Action</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<p>Stored: <strong>{state.get("stored_count", 0)}</strong> new &nbsp;|&nbsp; Scored: <strong>{len(scored_jobs)}</strong> total</p>
{sheet_link}
{error_block}
</body></html>"""


def build_slack_message(state: AgentState) -> str:
    """Slack mrkdwn — simple link notification."""
    sheet_url = state.get("sheet_url", "")
    ts = state.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d")
    count = len(state.get("scored_jobs", []))
    link = f"<{sheet_url}|Google Sheet>" if sheet_url else "Google Sheet"
    return f"Your daily job search results are ready ({count} matches on {ts}). View them here: {link}"


def build_telegram_message(state: AgentState) -> str:
    """Telegram plain text — simple link notification."""
    sheet_url = state.get("sheet_url", "")
    ts = state.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d")
    count = len(state.get("scored_jobs", []))
    lines = [
        f"Your daily job search results are ready ({count} matches on {ts}).",
        f"View them here: {sheet_url}",
    ]
    return "\n".join(lines)


_CHANNEL_FORMATTER = {
    "email": build_plain_message,
    "slack": build_slack_message,
    "telegram": build_telegram_message,
    "whatsapp": build_plain_message,
}


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    cfg = state["config"]
    notif_cfg = cfg.get("notifications", {})
    channels = notif_cfg.get("channels", [])

    sent = False
    for channel in channels:
        try:
            formatter = _CHANNEL_FORMATTER.get(channel, build_plain_message)
            message = formatter(state)

            if channel == "email":
                html_body = build_html_message(state)
            else:
                html_body = None

            from providers.notifications.factory import build_notifier
            notifier = build_notifier(channel, notif_cfg)
            notifier.send(message, html_body=html_body)
            run_log.append(f"Notification sent via {channel}")
            logger.info("Notification sent via %s", channel)
            sent = True
        except Exception as e:
            errors.append(f"Notification failed [{channel}]: {e}")
            logger.error("Notification failed [%s]: %s", channel, e)

    return {**state, "notification_sent": sent, "errors": errors, "run_log": run_log}
