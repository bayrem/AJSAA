"""SMTP email notifier.

Sends a multipart email (plain text + optional HTML) via the configured SMTP
server. Defaults target Gmail (smtp.gmail.com:587 STARTTLS) but any provider
that accepts STARTTLS will work — override ``EMAIL_SMTP_HOST`` and
``EMAIL_SMTP_PORT`` in ``.env``.

Required environment variables (see ``.env.template``):
  - ``EMAIL_FROM``      — sender address used for both From: header and SMTP login
  - ``EMAIL_TO``        — recipient address
  - ``EMAIL_PASSWORD``  — app-specific password (Gmail requires an App Password)
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from providers.notifications.base import BaseNotifier
from providers.utils import env_required

logger = logging.getLogger(__name__)


class EmailNotifier(BaseNotifier):
    """Send the run digest via SMTP as a multipart text+html email."""

    def __init__(self, cfg: dict) -> None:
        # Required credentials — fail fast if any are missing
        self.from_addr = env_required("EMAIL_FROM")
        self.to_addr = env_required("EMAIL_TO")
        self.password = env_required("EMAIL_PASSWORD")

        # Optional SMTP overrides; defaults target Gmail
        self.host = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
        self.port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))

        # Subject is configurable via config.yaml: notifications.email.subject
        self.subject = cfg.get("email", {}).get("subject", "AJSAA — Job Search Report")

    def send(self, message: str, html_body: str | None = None) -> None:
        """Send a multipart email. ``html_body`` is attached as the rich alternative."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = self.subject
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr

        # Attach plain text first; clients prefer the last attached part that
        # they can render, so HTML (attached second) wins when supported.
        msg.attach(MIMEText(message, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(self.host, self.port) as server:
            server.ehlo()
            server.starttls()
            server.login(self.from_addr, self.password)
            server.sendmail(self.from_addr, self.to_addr, msg.as_string())

        logger.info("Email notification sent to %s", self.to_addr)
