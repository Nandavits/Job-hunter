"""Sending emails (password reset). Free option: any SMTP account, e.g. a Gmail address with an app password
(Gmail allows about 500 emails a day). Without SMTP settings the link is written to the server log instead,
which is enough for running it on your own computer.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("jhcloud")
OUTBOX: list[EmailMessage] = []          # tests read sent emails from here when SMTP_HOST is "test"


def send(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER") or "jobhunter@localhost"
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    host = os.environ.get("SMTP_HOST", "")
    if host == "test":
        OUTBOX.append(msg)
        return
    if not host:
        log.warning("No SMTP_HOST set; email to %s not sent. Content:\n%s", to, body)
        return
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as smtp:
        smtp.starttls()
        smtp.login(os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
