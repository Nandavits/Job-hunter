"""Job collectors. Each returns a list of normalised `Job` objects."""
from __future__ import annotations

import re
import time
from typing import Callable

from bs4 import BeautifulSoup

from ..models import Job
from . import amazon, arbeitnow, arbeitsagentur, ats, databerlin, email_alerts
from .base import get, log, make_session

PAGE_SOURCES = {"personio", "greenhouse", "lever", "ashby"}   # Workday/SmartRecruiters read details themselves


def description_from_page(html: str) -> str:
    """Extract a job description from a public job page (used when a feed has none)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    blocks = soup.select('[class*="jobDescription"], [class*="job-description"], [class*="description"]')
    texts = []
    for block in blocks or [soup.body or soup]:
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        if len(text) > 40 and not any(text in t or t in text for t in texts):
            texts.append(text)
    return "\n".join(texts)[:20000]


LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d{6,})", re.I)


def description_for(session, url: str) -> str:
    """Read the full ad text from a job's public page. LinkedIn: its public (logged-out) job view."""
    m = LINKEDIN_JOB.search(url or "")
    if m:
        time.sleep(1.5)                                    # be gentle: a few pages per run only
        resp = get(session, f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}",
                   headers={"Accept": "text/html"})
        if resp is None:
            log.info("LinkedIn ad %s: page not available", m.group(1))
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        block = soup.select_one(".show-more-less-html__markup, .description__text, [class*='description']")
        return re.sub(r"\s+", " ", block.get_text(" ", strip=True))[:20000] if block else ""
    if not url:
        return ""
    resp = get(session, url, headers={"Accept": "text/html"})
    return description_from_page(resp.text) if resp is not None else ""


def fill_missing_descriptions(session, jobs: list[Job], limit: int = 40) -> None:
    """Some feeds (company boards, job-alert emails) have no ad text; read the job page instead."""
    for job in [j for j in jobs if (j.source in PAGE_SOURCES or j.source == "email")
                and len(j.description) < 100][:limit]:
        try:
            text = description_for(session, job.url)
        except Exception as exc:
            log.debug("No description for %s: %s", job.url, exc)
            continue
        if len(text) > len(job.description):
            job.description = text


def collect_all(settings, keep: Callable[[Job], bool] | None = None, session=None) -> list[Job]:
    """Run every enabled source. `keep` is a cheap title/location pre-filter."""
    session = session or make_session()
    sources = settings.sources
    jobs: list[Job] = []

    for company in sources.get("companies") or []:
        try:
            found = ats.fetch_company(session, company)
        except Exception as exc:  # one broken board must not stop the run
            log.warning("Failed %s: %s", company.get("name"), exc)
            continue
        log.info("%-20s %3d jobs", company.get("name", "?"), len(found))
        if keep:
            fill_missing_descriptions(session, [j for j in found if keep(j)])
        jobs.extend(found)

    an = sources.get("arbeitnow") or {}
    if an.get("enabled"):
        try:
            found = arbeitnow.fetch(session, an)
            if keep:
                fill_missing_descriptions(session, [j for j in found if keep(j)])
            jobs.extend(found)
        except Exception as exc:
            log.warning("Arbeitnow failed: %s", exc)

    amz = sources.get("amazon") or {}
    if amz.get("enabled"):
        try:
            jobs.extend(amazon.fetch(session, amz))
        except Exception as exc:
            log.warning("Amazon failed: %s", exc)

    dbn = sources.get("databerlin") or {}
    if dbn.get("enabled"):
        try:
            jobs.extend(databerlin.fetch(session, dbn, keep=keep))
        except Exception as exc:
            log.warning("Data Berlin failed: %s", exc)

    ba = sources.get("arbeitsagentur") or {}
    if ba.get("enabled"):
        try:
            found = arbeitsagentur.fetch(session, ba, keep=keep)
            log.info("%-20s %3d jobs", "Arbeitsagentur", len(found))
            jobs.extend(found)
        except Exception as exc:
            log.warning("Arbeitsagentur failed: %s", exc)

    mail = sources.get("email_alerts") or {}
    if mail.get("enabled"):
        try:
            found = email_alerts.fetch(mail, settings.env("GMAIL_ADDRESS"), settings.env("GMAIL_APP_PASSWORD"))
            if keep:
                fill_missing_descriptions(session, [j for j in found if keep(j)])
            jobs.extend(found)
        except Exception as exc:
            log.warning("Email alerts failed: %s", exc)

    return jobs
