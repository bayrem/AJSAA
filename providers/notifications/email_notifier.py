import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from providers.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


def _require(var: str) -> str:
    raise ValueError(f"{var} not set — add it to .env (see .env.template)")


class EmailNotifier(BaseNotifier):
    def __init__(self, cfg: dict):
        self.from_addr = os.environ.get("EMAIL_FROM") or _require("EMAIL_FROM")
        self.to_addr = os.environ.get("EMAIL_TO") or _require("EMAIL_TO")
        self.password = os.environ.get("EMAIL_PASSWORD") or _require("EMAIL_PASSWORD")
        self.host = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
        self.port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
        self.subject = cfg.get("email", {}).get("subject", "AJSAA — Job Search Report")

    def send(self, message: str, html_body: str | None = None) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = self.subject
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg.attach(MIMEText(message, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(self.host, self.port) as server:
            server.ehlo()
            server.starttls()
            server.login(self.from_addr, self.password)
            server.sendmail(self.from_addr, self.to_addr, msg.as_string())

        logger.info("Email notification sent to %s", self.to_addr)
