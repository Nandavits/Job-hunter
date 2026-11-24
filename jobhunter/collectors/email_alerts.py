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

from ..dates import normalize_date
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


GLASSDOOR_LINK = re.compile(r"glassdoor\.[a-z.]+/(?:partner/jobListing\.htm|job-listing/)[^\s\"']*?[?&](?:jobListingId|jl)=(\d+)")
GLASSDOOR_NOISE = re.compile(r"^(★|[\d.,]+\s*★|schnell bewerben|easy apply|einfach bewerben|neu|new|"
                             r"\d+\s*(?:t|tg|std|h|d|m|min|tage?|tagen|stunden?)\.?|\d+\+?\s*(?:t|d)|"
                             r".*[€$£].*|.*\(arbeitgeber.*|.*\(employer.*|.*schätz.*|.*est\.\)?)$", re.I)


def parse_glassdoor_html(html: str) -> list[Job]:
    """Glassdoor job-alert email: each job is one link holding company, title, location, salary, age."""
    if not html or "glassdoor" not in html.lower():
        return []
    soup = BeautifulSoup(html, "html.parser")   # keeps tables inside links (lxml would split them)
    jobs: dict[str, Job] = {}
    for a in soup.find_all("a", href=True):
        m = GLASSDOOR_LINK.search(a["href"])
        if not m:
            continue
        parts = []
        for text in a.stripped_strings:
            text = re.sub(r"\s+[\d.,]+\s*★\s*$", "", text.replace("\ufeff", "")).strip(" |")
            if text and not GLASSDOOR_NOISE.match(text) and text not in parts:
                parts.append(text)
        if len(parts) < 2:
            continue
        company, title = parts[0], parts[1]
        location = parts[2] if len(parts) > 2 else ""
        key = f"glassdoor:{m.group(1)}"
        jobs.setdefault(key, Job(source="email", external_id=key, company=company[:120], title=title[:200],
                                 url=f"https://www.glassdoor.de/job-listing/j?jl={m.group(1)}",
                                 location=location[:120], extra={"site": "glassdoor", "needs_review": True}))
    return list(jobs.values())


# Job boards whose alert emails are read with the general parser below: sender-address part -> site name.
BOARD_SENDERS = {
    "jobvector": "jobvector", "yourfirm": "yourfirm", "jobware": "jobware", "goodjobs": "goodjobs",
    "kimeta": "kimeta", "empfehlungsbund": "empfehlungsbund", "interamt": "interamt",
    "karriere-familienunternehmen": "karriere-familienunternehmen", "entrepreneursclub": "karriere-familienunternehmen",
    "joblift": "joblift",
    "arbeitnow": "arbeitnow",
}
NOT_A_JOB_LINK = re.compile(
    r"^(alle|weitere|mehr|more|all|show|see|view all|zur übersicht|übersicht|job[ -]?mail|job[ -]?alert|jobagent|"
    r"such(auftrag|agent|profil)|abmelden|abbestellen|unsubscribe|einstellungen|settings|profil|profile|"
    r"login|anmelden|registrieren|konto|datenschutz|privacy|impressum|imprint|agb|nutzungsbedingungen|terms|"
    r"kontakt|contact|hilfe|help|faq|newsletter|app|download|facebook|instagram|linkedin|xing|twitter|youtube|"
    r"tiktok|im browser|web ?version|online ansehen|hier klicken|click here|zur website|startseite|home|"
    r"merken|speichern|teilen|share|feedback|ratgeber|karriere-?tipps|gehalt|bestätig|aktivier|confirm|activate|"
    r"löschen|delete|verwalten|manage|bearbeiten|edit|abonnier|subscribe|newsletter)", re.I)
QUICK_APPLY = re.compile(r"^(schnellbewerbung|quick apply|easy apply|top[- ]?job|neu|new)$", re.I)
JOB_BUTTON = re.compile(r"^(zur (stelle|stellenanzeige|anzeige)|zum job|job ansehen|stelle ansehen|details|"
                        r"anzeige (ansehen|öffnen)|mehr erfahren|jetzt bewerben|bewerben|view job|apply)\b", re.I)
SOCIAL_HOSTS = re.compile(r"facebook\.|instagram\.|twitter\.|x\.com|youtube\.|tiktok\.|linkedin\.com/company|"
                          r"xing\.com/pages|apps\.apple\.|play\.google\.", re.I)


def _is_navigation(label: str) -> bool:
    """Menu/settings links are short; a long label or one with (m/w/d) is a job title even if it
    starts with a word like 'Datenschutz' (Datenschutzkoordinator ...)."""
    if re.search(r"\([mwfdx]/", label, re.I):
        return False
    if re.search(r"bestätig|aktivier|\bconfirm|\bactivate|abbestell|unsubscribe", label, re.I):
        return True                     # "E-Mail-Adresse bestätigen und Job-Newsletter aktivieren"
    return bool(NOT_A_JOB_LINK.match(label)) and len(label) <= 40


def board_for_sender(sender: str) -> str:
    sender = (sender or "").lower()
    return next((site for key, site in BOARD_SENDERS.items() if key in sender), "")


def parse_board_html(html: str, site: str) -> list[Job]:
    """General reader for job-board alert emails (jobvector, Jobware, kimeta, Interamt ...).
    Each job is a link whose text is the job title (or a 'view job' button under the title);
    company and location are the next lines in the same block. Navigation links are skipped."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, Job] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http") or SOCIAL_HOSTS.search(href) or re.match(r"https?://[^/]+/?$", href):
            continue                                       # social links and plain home-page links
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        own = [l for l in _lines(a) if not NOISE_LINE.match(l) and not QUICK_APPLY.match(l)]
        if len(own) >= 2 and not JOB_BUTTON.match(own[0]):
            # the whole job card is one link (jobvector): title, company, salary, town on separate lines
            title, rest = own[0], [l for l in own[1:] if not _is_navigation(l)]
            key = _job_id(site, href, title, rest[0] if rest else "")
            if 8 <= len(title) <= 200 and not _is_navigation(title):
                jobs.setdefault(key, Job(source="email", external_id=key, company=(rest[0] if rest else "")[:120],
                                         title=title[:200], url=href,
                                         location=(rest[-1] if len(rest) > 1 else "")[:120],
                                         extra={"site": site, "needs_review": True}))
            continue
        block = a.find_parent(["td", "tr", "div", "li", "table"]) or a
        lines = [l for l in _lines(block) if not NOISE_LINE.match(l) and not JOB_BUTTON.match(l)]
        if JOB_BUTTON.match(label):
            title = lines[0] if lines else ""
        else:
            title = label
        if label and len(lines) <= 1:
            # title alone in its cell, company and town in the next rows of the same card (kimeta)
            card = a.find_parent("table")
            if card is not None:
                after = _lines(card)
                if title in after:
                    after = after[after.index(title) + 1:]
                    lines = [title] + [l for l in after if not NOISE_LINE.match(l)][:2]
        if not (8 <= len(title) <= 200) or _is_navigation(title) or JOB_BUTTON.match(title) \
                or not re.search(r"[A-Za-zÄÖÜäöüß]{3}", title) or URL_RE.match(title) \
                or re.sub(r"[^a-z]", "", title.lower()) in (site.replace("-", ""), re.sub(r"[^a-z]", "", site + "de")):
            continue
        rest = [l for l in lines if l != title and not _is_navigation(l)]
        company = rest[0] if rest else ""
        location = rest[1] if len(rest) > 1 else ""
        key = _job_id(site, href, title, company)
        jobs.setdefault(key, Job(source="email", external_id=key, company=company[:120], title=title[:200],
                                 url=href, location=location[:120], extra={"site": site, "needs_review": True}))
    return list(jobs.values())


MD_LINK = re.compile(r"\[\s*([^\]\n]{3,200}?)\s*\]\((https?://[^)\s]+)[^)]*\)")


def _clean_url(url: str) -> str:
    return re.sub(r"[?&]utm_[^#]*$", "", url)


def parse_board_text(text: str, site: str) -> list[Job]:
    """Plain-text part of job-board alerts written as [Job title](link), then company and location lines
    (yourfirm and others). Gives the board's own job link instead of a tracking link."""
    jobs: dict[str, Job] = {}
    matches = list(MD_LINK.finditer(text or ""))
    for i, m in enumerate(matches):
        title, url = re.sub(r"[*_]+", "", m.group(1)).strip(), m.group(2)
        if not (8 <= len(title) <= 200) or _is_navigation(title) or JOB_BUTTON.match(title):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        lines = [l.strip(" |*") for l in text[m.end():end].splitlines()]
        lines = [l for l in lines if l and not NOISE_LINE.match(l) and not _is_navigation(l)][:2]
        if not lines:                       # a link with nothing under it is a menu link, not a job
            continue
        company = lines[0]
        location = lines[1] if len(lines) > 1 else ""
        key = _job_id(site, url, title, company)
        jobs.setdefault(key, Job(source="email", external_id=key, company=company[:120], title=title[:200],
                                 url=_clean_url(url), location=location[:120],
                                 extra={"site": site, "needs_review": True}))
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
                html_body = _body_html(msg)
                board = board_for_sender(msg.get("From", "") or sender)
                if board:
                    found = parse_board_text(_body_text(msg), board) or parse_board_html(html_body, board)
                else:
                    found = parse_glassdoor_html(html_body) or parse_alert_text(_body_text(msg)) or \
                        parse_alert_html(html_body)
                sent = normalize_date(msg.get("Date", ""))
                for job in found:
                    job.extra["email_subject"] = subject
                    job.posted_at = job.posted_at or sent      # alert emails list jobs from the last day
                    jobs.setdefault(job.external_id, job)
    log.info("Email alerts: %d jobs", len(jobs))
    return list(jobs.values())
