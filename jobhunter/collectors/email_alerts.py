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


URL_RE = re.compile(r"https?://\S+")
JOB_HOSTS = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/|click\.stepstone\.de/|stepstone\.de/stellenangebote|"
                       r"indeed\.[a-z.]+/(?:rc/clk|viewjob|pagead)|xing\.com/(?:jobs/|m/)", re.I)
NOT_JOB_LABEL = re.compile(r"^(see all|manage|unsubscribe|help|terms|privacy|contact|facebook|instagram|youtube|"
                           r"twitter|tiktok|blog|xing|learn why|edit|update profile|view message|change|"
                           r"read the full|report this|because you viewed)", re.I)
NOISE_LINE = re.compile(
    r"^(hi |hallo |here.s a job|recommended for you|your job alert|\d+\+? new jobs|i.m interested|"
    r"this company is actively hiring|\d+ school alums?|\d+ connections?|actively recruiting|promoted|easy apply|"
    r"boost your chances|you.re also a good match|view job|apply|jetzt bewerben|"
    r"\d+ (?:days?|hours?|weeks?) ago|vor \d+|new$|neu$|[\d.,+]+ employees|befristet|unbefristet|"
    r"teilzeit|vollzeit|minijob$|homeoffice|remote$|[\d .,–-]+€|.*€/jahr|.*\(geschätzt)", re.I)


def _job_id(site: str, url: str, title: str, company: str) -> str:
    if site == "linkedin":
        m = re.search(r"jobs/view/(\d+)", url)
        if m:
            return f"linkedin:{m.group(1)}"
    if site == "indeed":
        m = re.search(r"[?&]jk=([0-9a-f]+)", url)
        if m:
            return f"indeed:{m.group(1)}"
    # tracking links change per email, so identify the job by its title + company
    key = re.sub(r"\W+", " ", f"{title} {company}".lower()).strip()
    return f"{site}:{key}"


def _site(url: str) -> str:
    for name in ("linkedin", "stepstone", "indeed", "xing"):
        if name in url.lower():
            return name
    return "email"


def parse_alert_text(text: str) -> list[Job]:
    """Parse the plain-text part of alert emails: blocks of title / company / location, then a link."""
    jobs: dict[str, Job] = {}
    buffer: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if set(line) <= set("-~=_ "):  # separator line
            buffer = []
            continue
        url_match = URL_RE.search(line)
        if not url_match:
            if not NOISE_LINE.match(line):
                buffer.append(line)
            continue
        url = url_match.group(0).rstrip(").,]>")
        label = line[:url_match.start()].strip(" :[")
        is_job_link = JOB_HOSTS.search(url) and (not label or label.lower().startswith(("view job", "apply")))
        if label and not label.lower().startswith(("view job", "apply")) and not NOT_JOB_LABEL.match(label):
            buffer.append(label)
            is_job_link = False
        if is_job_link and 2 <= len(buffer) <= 6:
            title, company = buffer[0], buffer[1]
            location = buffer[2] if len(buffer) > 2 else ""
            site = _site(url)
            clean = CLEAN_URLS.get(site, "")
            job_key = _job_id(site, url, title, company)
            if clean and job_key.split(":", 1)[1].isalnum():
                url = clean.format(id=job_key.split(":", 1)[1])
            jobs.setdefault(job_key, Job(source="email", external_id=job_key, company=company[:120],
                                         title=title[:200], url=url, location=location[:120],
                                         extra={"site": site, "needs_review": True}))
        buffer = []
    return list(jobs.values())


def _body_text(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


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
                found = parse_alert_text(_body_text(msg)) or parse_alert_html(_body_html(msg))
                for job in found:
                    job.extra["email_subject"] = subject
                    jobs.setdefault(job.external_id, job)
    log.info("Email alerts: %d jobs", len(jobs))
    return list(jobs.values())
