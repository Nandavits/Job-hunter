"""Read job-alert emails (LinkedIn, StepStone, Indeed, XING, ...) from your own inbox.

This never logs in to those sites: it only reads the alert emails they already send you,
via IMAP with a Gmail App Password. Alert emails rarely contain the full job description,
so these jobs are filtered on title/company/location and marked for manual review.
"""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header

from bs4 import BeautifulSoup

from ..models import Job
from .base import log

# (site, regex on the link href, group that holds a stable id)
LINK_PATTERNS = [
    ("linkedin", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")),
    ("stepstone", re.compile(r"stepstone\.de/stellenangebote--[^\s\"']*?-(\d+)-inline\.html")),
    ("indeed", re.compile(r"indeed\.[a-z.]+/(?:rc/clk|viewjob|pagead/clk|m/viewjob)[^\s\"']*?[?&]jk=([0-9a-f]+)")),
    ("xing", re.compile(r"xing\.com/jobs/[^\s\"'?]*?-(\d+)")),
]

CLEAN_URLS = {
    "linkedin": "https://www.linkedin.com/jobs/view/{id}/",
    "indeed": "https://de.indeed.com/viewjob?jk={id}",
}


def _lines(tag) -> list[str]:
    text = tag.get_text("\n", strip=True)
    return [l.strip() for l in text.split("\n") if l.strip()]


def parse_alert_html(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs: dict[str, Job] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for site, pattern in LINK_PATTERNS:
            m = pattern.search(href)
            if not m:
                continue
            job_id = m.group(1)
            title = a.get_text(" ", strip=True)
            if len(title) < 4 or title.lower() in {"view job", "apply", "jetzt bewerben", "see all jobs"}:
                break
            # company / location usually follow the title in the same table cell
            block = a.find_parent(["td", "div", "tr"]) or a
            lines = [l for l in _lines(block) if l != title]
            company = lines[0] if lines else ""
            location = lines[1] if len(lines) > 1 else ""
            key = f"{site}:{job_id}"
            if key not in jobs:
                url = CLEAN_URLS.get(site, "").format(id=job_id) or href.split("?")[0]
                jobs[key] = Job(source="email", external_id=key, company=company[:120],
                                title=title[:200], url=url, location=location[:120],
                                extra={"site": site, "needs_review": True})
            break
    return list(jobs.values())


def _body_html(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def fetch(cfg: dict, address: str, app_password: str) -> list[Job]:
    if not (address and app_password):
        log.warning("Email alerts enabled but GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set")
        return []
    since = (datetime.now() - timedelta(days=cfg.get("days_back", 2))).strftime("%d-%b-%Y")
    jobs: dict[str, Job] = {}
    with imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.gmail.com")) as imap:
        imap.login(address, app_password)
        imap.select(cfg.get("folder", "INBOX"), readonly=True)  # read-only: nothing is changed
        for sender in cfg.get("senders", []):
            status, data = imap.search(None, "SINCE", since, "FROM", f'"{sender}"')
            if status != "OK":
                continue
            for num in data[0].split():
                _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(msg_data[0][1])
                subject = str(make_header(decode_header(msg.get("Subject", ""))))
                for job in parse_alert_html(_body_html(msg)):
                    job.extra["email_subject"] = subject
                    jobs.setdefault(job.external_id, job)
    log.info("Email alerts: %d jobs", len(jobs))
    return list(jobs.values())
