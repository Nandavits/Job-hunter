"""Find the company's own application page for jobs seen on LinkedIn / StepStone / XING / Indeed.

Applying on the employer's careers site means no automation ever touches your job-board accounts.
Two ways, cheapest first:
  1. LinkedIn's public job view often names the employer's own "Apply" link.
  2. Otherwise look for the company's board on Greenhouse, Lever, Ashby and Personio (slugs guessed
     from the company name) and pick the posting with the same title.
Only public, logged-out pages are read, a few per run.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .collectors import LINKEDIN_JOB, ats
from .collectors.base import get
from .collectors.base import log as _base_log
from .dedupe import normalize_company, same_title

log = logging.getLogger("jobhunter")
base_log_name = _base_log.name

JOB_BOARD_HOSTS = ("linkedin.", "stepstone.", "xing.", "indeed.", "glassdoor.", "jobs.ch", "monster.")
NOT_FOUND_NOTE = "no company careers link found"


def is_job_board(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return any(h in host for h in JOB_BOARD_HOSTS)


def linkedin_offsite_link(html: str) -> str:
    """The employer's own apply link on a LinkedIn public job page, if it has one."""
    soup = BeautifulSoup(html, "lxml")
    candidates = []
    code = soup.select_one("code#applyUrl")
    if code:
        candidates.append(code.decode_contents())
    candidates += [a.get("href", "") for a in soup.select("a[href*='externalApply'], a[data-tracking-control-name*='offsite']")]
    for raw in candidates:
        m = re.search(r"https?://[^\s\"'<>]+", raw.replace("&amp;", "&"))
        if not m:
            continue
        link = m.group(0)
        target = parse_qs(urlparse(link).query).get("url")
        link = unquote(target[0]) if target else link
        if link.startswith("http") and not is_job_board(link):
            return link
    return ""


def slug_guesses(company: str) -> list[str]:
    """'CKM Group GmbH' -> ['ckmgroup', 'ckm-group', 'ckm']."""
    legal = {"gmbh", "ag", "se", "kg", "co", "ug", "mbh", "inc", "ltd", "llc", "bv", "sa", "kgaa", "ohg", "ev"}
    words = [w for w in re.findall(r"[a-z0-9]+", (company or "").lower().replace("ä", "ae")
                                    .replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")) if w not in legal]
    if not words:
        return []
    short = re.findall(r"[a-z0-9]+", normalize_company(company))
    guesses = ["".join(words), "-".join(words), "".join(short), words[0]]
    return [g for g in dict.fromkeys(guesses) if len(g) >= 3]


def find_on_company_boards(session, company: str, title: str) -> str:
    quiet = logging.getLogger(base_log_name)
    level = quiet.level
    quiet.setLevel(logging.ERROR)            # most guesses do not exist; that is expected, not a warning
    try:
        return _search_boards(session, company, title)
    finally:
        quiet.setLevel(level)


def _search_boards(session, company: str, title: str) -> str:
    for slug in slug_guesses(company):
        for system in ("greenhouse", "lever", "ashby", "personio"):
            try:
                jobs = ats.fetch_company(session, {"name": company, "ats": system, "slug": slug})
            except Exception:
                continue
            for job in jobs:
                if same_title(job.title, title):
                    return job.apply_url or job.url
    return ""


def find_career_link(session, job: dict) -> str:
    url = job.get("url") or ""
    m = LINKEDIN_JOB.search(url)
    if m:
        time.sleep(1.5)
        resp = get(session, f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}",
                   headers={"Accept": "text/html"})
        if resp is not None:
            link = linkedin_offsite_link(resp.text)
            if link:
                return link
    return find_on_company_boards(session, job.get("company") or "", job.get("title") or "")


def add_career_links(store, session, min_score: int, limit: int = 10) -> int:
    """For good job-board matches without a company link yet, look for the employer's own page."""
    rows = [r for r in store.list(limit=100_000)
            if r["status"] in ("new", "prepared") and (r["score"] or 0) >= min_score
            and is_job_board(r["url"]) and not (r.get("apply_url") and not is_job_board(r["apply_url"]))
            and NOT_FOUND_NOTE not in (r.get("notes") or "")]
    rows.sort(key=lambda r: -(r["score"] or 0))
    found = 0
    for row in rows[:limit]:
        try:
            link = find_career_link(session, row)
        except Exception as exc:
            log.debug("Career link lookup failed for %s: %s", row["company"], exc)
            continue
        if link:
            store.update(row["uid"], apply_url=link)
            found += 1
            log.info("Company link for %s - %s: %s", row["company"], row["title"], link)
        else:
            note = ((row.get("notes") or "") + f" | {NOT_FOUND_NOTE}").strip(" |")
            store.update(row["uid"], notes=note)
    if rows:
        log.info("Found the company's own application page for %d of %d job-board matches", found, len(rows[:limit]))
    return found
