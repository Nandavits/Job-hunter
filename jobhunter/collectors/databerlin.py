"""Data Berlin (databerlin.net): Berlin data & AI community board with ~1,000 jobs taken from company
career pages. The list page is plain HTML; each job page has the full ad and the employer's own
application link (Greenhouse, Lever, Ashby, Personio ...), which becomes the job's apply link.

Polite use: one list page per run, and job pages only for titles that fit your target roles.
"""
from __future__ import annotations

import re
import time
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import Job
from .base import get, log

BASE = "https://databerlin.net"
JOB_PATH = re.compile(r"^/jobs/(?!category/)([a-z0-9-]+-at-[a-z0-9-]+)/?$")
PLACES = re.compile(r"\b(Berlin|Hamburg|Munich|München|Frankfurt|Cologne|Köln|Leipzig|Potsdam|Germany|Deutschland|"
                    r"Remote|London|United Kingdom|UK|Amsterdam|Netherlands|Paris|France|Vienna|Austria|Zurich|"
                    r"Switzerland|Warsaw|Poland|Barcelona|Madrid|Spain|Lisbon|Portugal|Dublin|Ireland|Europe|EMEA)\b")
EXTERNAL_APPLY = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|personio\.(de|com)|workable\.com|"
                            r"smartrecruiters\.com|recruitee\.com|join\.com|myworkdayjobs\.com|teamtailor\.com|"
                            r"successfactors|/apply|/jobs?/|/careers?/", re.I)


def _company_from_slug(slug: str) -> str:
    name = slug.rsplit("-at-", 1)[-1].replace("-", " ").title()
    return re.sub(r"(\s+\d+)+$", "", name)          # "about-you-6" -> "About You"


def _soup(content) -> BeautifulSoup:
    """Parse from bytes so umlauts are decoded as UTF-8 (not guessed)."""
    return BeautifulSoup(content, "lxml")


def parse_list(html) -> list[Job]:
    soup = _soup(html)
    jobs: dict[str, Job] = {}
    for a in soup.find_all("a", href=True):
        path = urlparse(a["href"]).path if a["href"].startswith("http") else a["href"].split("?")[0]
        m = JOB_PATH.match(path)
        if not m or m.group(1) in jobs:
            continue
        slug = m.group(1)
        lines = [x.strip() for x in a.get_text("\n", strip=True).split("\n") if x.strip()]
        title = lines[0] if lines else slug.rsplit("-at-", 1)[0].replace("-", " ").title()
        card = a
        for _ in range(4):                                   # climb to the job's card for company/location
            if card.parent is None or len(card.parent.get_text(" ", strip=True)) > 600:
                break
            card = card.parent
        card_text = card.get_text(" · ", strip=True)
        company_link = card.find("a", href=re.compile(r"/companies/"))
        company = company_link.get_text(strip=True) if company_link else _company_from_slug(slug)
        places = list(dict.fromkeys(PLACES.findall(card_text)))
        jobs[slug] = Job(
            source="databerlin",
            external_id=slug,
            company=company,
            title=title,
            url=urljoin(BASE, f"/jobs/{slug}"),
            location=", ".join(places) or "Berlin",
            remote="Remote" in places,
        )
    return list(jobs.values())


def parse_detail(html) -> tuple[str, str, str]:
    """(description, employer's application link, company name) from a job page."""
    soup = _soup(html)
    company_link = soup.find("a", href=re.compile(r"/companies/[a-z0-9-]+/?$"))
    company = company_link.get_text(" ", strip=True) if company_link else ""
    if len(company) > 60 or company.lower().startswith(("view", "see", "all ")):
        company = ""
    apply = ""
    for a in soup.find_all("a", href=True):
        href, text = a["href"], a.get_text(" ", strip=True).lower()
        if not href.startswith("http") or "databerlin.net" in href:
            continue
        if ("apply" in text or "bewerb" in text or "original" in text) and EXTERNAL_APPLY.search(href):
            apply = href
            break
    if not apply:   # no labelled button: first link that points at an applicant-tracking system
        for a in soup.find_all("a", href=True):
            if re.search(r"greenhouse\.io|lever\.co|ashbyhq\.com|personio\.|workable\.com|smartrecruiters", a["href"]):
                apply = a["href"]
                break
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = re.sub(r"\s+", " ", main.get_text(" ", strip=True))[:20000]
    apply = re.sub(r"[?&]utm_[^&]+", "", apply).replace("?&", "?").rstrip("?&")
    return text, apply, company


def fetch(session, cfg: dict, keep: Callable[[Job], bool] | None = None) -> list[Job]:
    resp = get(session, f"{BASE}/jobs", headers={"Accept": "text/html"})
    if resp is None:
        log.warning("Data Berlin: job list not available")
        return []
    jobs = parse_list(resp.content)
    wanted = [j for j in jobs if keep is None or keep(j)]
    for job in wanted[: int(cfg.get("max_details", 80))]:
        time.sleep(float(cfg.get("delay_seconds", 1.0)))
        page = get(session, job.url, headers={"Accept": "text/html"})
        if page is None:
            continue
        job.description, job.apply_url, company = parse_detail(page.content)
        job.company = company or job.company
    log.info("%-20s %3d jobs (%d read in full)", "Data Berlin", len(jobs), min(len(wanted), int(cfg.get("max_details", 80))))
    return jobs
