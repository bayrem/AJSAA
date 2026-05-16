"""Send run summary via configured notification channels.

Each channel (email / slack / telegram / whatsapp) has its own formatter
because the rendering rules are very different — Slack uses mrkdwn, email
needs HTML, Telegram is plain text, etc. A small dispatch table maps the
channel name to its formatter; email additionally gets a rich HTML body.

Failures on one channel do not prevent others from running — channel errors
are recorded to ``state["errors"]`` but the node continues.
"""
import html as html_mod
import logging
from datetime import datetime, timezone

from agent.state import AgentState

logger = logging.getLogger(__name__)


# How many top jobs to include in each digest. Configurable in a future
# config.yaml field if needed.
_TOP_N = 5


# ── Plain-text formatters ────────────────────────────────────────────────────

def _top_jobs(scored_jobs: list[dict]) -> list[dict]:
    """Return the top-N scored jobs to surface in the digest."""
    return scored_jobs[:_TOP_N]


def _timestamp(state: AgentState) -> str:
    """Return the run timestamp, falling back to today's UTC date."""
    return state.get("timestamp") or datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build_plain_message(state: AgentState) -> str:
    """Plain-text digest. Used by email (text part) and as fallback elsewhere."""
    scored_jobs = state.get("scored_jobs", [])
    top = _top_jobs(scored_jobs)
    errors = state.get("errors", [])

    lines = [
        f"AJSAA — Run Report ({_timestamp(state)})",
        f"New jobs stored : {state.get('stored_count', 0)}",
        f"Total scored    : {len(scored_jobs)}",
    ]

    if top:
        lines.append("\nTop Matches:")
        for j in top:
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


# ── HTML formatter — split into row + table builders for readability ─────────

def _html_safe_url(url: str) -> str:
    """Return an escaped URL if it's http(s), else ``"#"`` (defangs javascript:)."""
    if url.startswith(("https://", "http://")):
        return html_mod.escape(url, quote=True)
    return "#"


def _job_row_html(job: dict) -> str:
    """Build one ``<tr>`` for the top-jobs table."""
    raw_url = job.get("url", "")
    safe_url = _html_safe_url(raw_url)

    title = html_mod.escape(job.get("title", ""))
    # Linkify the title only when we have a usable URL — otherwise plain text
    title_cell = f'<a href="{safe_url}">{title}</a>' if safe_url != "#" else title

    # Score-driven badge colour: green for solid matches, orange otherwise
    badge_color = "#2e7d32" if job.get("score", 0) >= 80 else "#f57c00"

    return (
        "<tr>"
        f'<td style="padding:6px 10px;">'
        f'<span style="background:{badge_color};color:#fff;border-radius:4px;padding:2px 6px;font-size:13px;">{job.get("score")}</span>'
        f"</td>"
        f'<td style="padding:6px 10px;">{title_cell}</td>'
        f'<td style="padding:6px 10px;">{html_mod.escape(job.get("company", ""))}</td>'
        f'<td style="padding:6px 10px;">{html_mod.escape(job.get("recommendation", ""))}</td>'
        "</tr>"
    )


def _sheet_link_html(sheet_url: str | None) -> str:
    """Render the optional "open the sheet" link, if a sheet URL exists."""
    if not sheet_url:
        return ""
    if sheet_url.startswith(("https://", "http://")):
        return f'<p><a href="{html_mod.escape(sheet_url)}">Open Google Sheet →</a></p>'
    # Non-URL sheet identifier — display as text rather than a broken link
    return f"<p>Google Sheet: {html_mod.escape(sheet_url)}</p>"


def _error_block_html(error_count: int) -> str:
    """Render the optional yellow error banner if there were any errors."""
    if not error_count:
        return ""
    return (
        f'<p style="color:#c62828;">⚠ {error_count} error(s) — '
        'check logs/job_search.log</p>'
    )


def build_html_message(state: AgentState) -> str:
    """HTML digest — used as the rich alternative for email."""
    scored_jobs = state.get("scored_jobs", [])
    top = _top_jobs(scored_jobs)
    errors = state.get("errors", [])

    rows = "".join(_job_row_html(j) for j in top)
    sheet_link = _sheet_link_html(state.get("sheet_url", ""))
    error_block = _error_block_html(len(errors))

    return f"""<html><body style="font-family:sans-serif;color:#222;">
<h2 style="margin-bottom:4px;">AJSAA — Run Report</h2>
<p style="color:#666;margin-top:0;">{_timestamp(state)}</p>
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


# ── Slack & Telegram short notifications ─────────────────────────────────────

def build_slack_message(state: AgentState) -> str:
    """Slack mrkdwn — single-line notification with link to the sheet."""
    sheet_url = state.get("sheet_url", "")
    count = len(state.get("scored_jobs", []))
    link = f"<{sheet_url}|Google Sheet>" if sheet_url else "Google Sheet"
    return (
        f"Your daily job search results are ready ({count} matches on "
        f"{_timestamp(state)}). View them here: {link}"
    )


def build_telegram_message(state: AgentState) -> str:
    """Telegram plain text — single-line notification + URL on next line."""
    sheet_url = state.get("sheet_url", "")
    count = len(state.get("scored_jobs", []))
    return "\n".join([
        f"Your daily job search results are ready ({count} matches on {_timestamp(state)}).",
        f"View them here: {sheet_url}",
    ])


# Channel → plain-text formatter. Email and whatsapp both reuse the
# multi-line plain digest; slack and telegram have their own one-liners.
_CHANNEL_FORMATTER = {
    "email": build_plain_message,
    "slack": build_slack_message,
    "telegram": build_telegram_message,
    "whatsapp": build_plain_message,
}


# ── Graph node ───────────────────────────────────────────────────────────────

def run(state: AgentState) -> AgentState:
    """Dispatch the digest to every configured notification channel."""
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

            # HTML alternative is only produced for email — every other
            # channel ignores html_body anyway, so we save the work.
            html_body = build_html_message(state) if channel == "email" else None

            # Lazy import — keeps optional notification deps off the path
            # when the channel isn't configured.
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
