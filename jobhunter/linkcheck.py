"""Is the application page still open? Checks the company's job board for every open job, so jobs the
company has closed leave your list before you click a dead link.

Only definite answers count: the board answered and the job is not on it (or the job page says 404).
If a board can't be reached, the job stays open.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("jobhunter")

CLOSED = "job closed (application page gone)"
AD_CLOSED = "job closed (ad says it no longer accepts applications)"
# What job pages say once a job is closed (LinkedIn, StepStone, Indeed, XING, company pages).
CLOSED_TEXT = re.compile(
    r"no longer accepting applications|this job (?:is|has) (?:no longer available|expired|been closed)|"
    r"(?:job|position|posting|vacancy) (?:has been filled|is (?:closed|no longer (?:open|available)))|"
    r"(?:job|stellen)anzeige ist (?:nicht mehr|abgelaufen)|stelle ist (?:leider )?(?:nicht mehr (?:verfügbar|aktiv)|"
    r"bereits besetzt)|(?:diese|das) stellenangebot ist nicht mehr", re.I)
RECHECK_HOURS = 12


def _ids_on_board(session, kind: str, org: str, cache: dict):
    key = (kind, org)
    if key in cache:
        return cache[key]
    ids = None
    try:
        if kind == "greenhouse":
            r = session.get(f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs", timeout=20)
            if r.status_code == 200:
                ids = {str(j.get("id")) for j in r.json().get("jobs", [])}
        elif kind in ("lever", "lever-eu"):
            api = "api.eu.lever.co" if kind == "lever-eu" else "api.lever.co"
            r = session.get(f"https://{api}/v0/postings/{org}?mode=json", timeout=20)
            if r.status_code == 200:
                ids = {j.get("id") for j in r.json()}
        elif kind == "ashby":
            r = session.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}", timeout=20)
            if r.status_code == 200:
                ids = {j.get("id") for j in r.json().get("jobs", [])}
        elif kind == "personio":
            r = session.get(f"https://{org}.jobs.personio.de/xml", timeout=20)
            if r.status_code == 200:
                ids = set(re.findall(r"<id>(\d+)</id>", r.text))
        elif kind == "recruitee":
            r = session.get(f"https://{org}.recruitee.com/api/offers/", timeout=20)
            if r.status_code == 200:
                ids = {o.get("slug") for o in r.json().get("offers", [])}
    except Exception as exc:
        log.debug("Board check failed for %s %s: %s", kind, org, exc)
    cache[key] = ids
    return ids


def is_closed(session, apply_url: str, cache: dict) -> bool | None:
    """True = definitely closed, False = open, None = can't tell."""
    url = apply_url or ""
    parts = urlparse(url)
    host, path = parts.netloc.lower(), parts.path
    if "greenhouse.io" in host:
        q = parse_qs(parts.query)
        org, token = (q.get("for") or [None])[0], (q.get("token") or [None])[0]
        m = re.search(r"^/([^/]+)/jobs/(\d+)", path)
        if m:
            org, token = m.group(1), m.group(2)
        if org and token:
            ids = _ids_on_board(session, "greenhouse", org, cache)
            return None if ids is None else token not in ids
    if "lever.co" in host:
        m = re.search(r"^/([^/]+)/([0-9a-f-]{36})", path)
        if m:
            ids = _ids_on_board(session, "lever-eu" if ".eu." in host else "lever", m.group(1), cache)
            return None if ids is None else m.group(2) not in ids
    if host == "jobs.ashbyhq.com":
        m = re.search(r"^/([^/]+)/([0-9a-f-]{36})", path)
        if m:
            ids = _ids_on_board(session, "ashby", m.group(1), cache)
            return None if ids is None else m.group(2) not in ids
    if host.endswith(".jobs.personio.de") or host.endswith(".jobs.personio.com"):
        m = re.search(r"/job/(\d+)", path)
        if m:
            ids = _ids_on_board(session, "personio", host.split(".")[0], cache)
            return None if ids is None else m.group(1) not in ids
    if host.endswith(".recruitee.com"):
        m = re.search(r"^/o/([^/]+)", path)
        if m:
            ids = _ids_on_board(session, "recruitee", host.split(".")[0], cache)
            return None if ids is None else m.group(1) not in ids
    return None


def ad_closed(session, url: str) -> bool | None:
    """Does the job's own page say it is closed? True = it says so, None = page looks open or can't be read."""
    from .collectors import LINKEDIN_JOB
    url = url or ""
    m = LINKEDIN_JOB.search(url)
    if m:                                   # LinkedIn's public (logged-out) view of the job
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}"
    elif not url.startswith("http") or "glassdoor." in url:
        return None
    try:
        r = session.get(url, headers={"Accept": "text/html"}, timeout=15)
    except Exception as exc:
        log.debug("Could not open %s: %s", url, exc)
        return None
    if r.status_code != 200:
        return None                         # LinkedIn also answers 404/429 when it is busy: can't tell
    return True if CLOSED_TEXT.search(r.text[:400_000]) else None


def _checked(path) -> dict:
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def close_closed_ads(store, session, limit: int = 40, checked_file=None, now=None) -> int:
    """Open jobs whose ad page now says "no longer accepting applications" (LinkedIn alerts, job boards)
    leave your list. Each job is looked at at most every 12 hours, a few pages per run."""
    import datetime as dt
    import json
    import time
    from .config import DATA_DIR
    checked_file = checked_file or DATA_DIR / "ad-checks.json"
    now = now or dt.datetime.now()
    checked = _checked(checked_file)
    due = []
    for r in store.list(limit=100_000):
        if r["status"] not in ("new", "prepared"):
            continue
        last = checked.get(r["uid"], "")
        if last and now - dt.datetime.fromisoformat(last) < dt.timedelta(hours=RECHECK_HOURS):
            continue
        due.append(r)
    due.sort(key=lambda r: -(r.get("score") or 0))          # the jobs at the top of your list first
    closed = 0
    for row in due[:limit]:
        for url in dict.fromkeys(u for u in (row.get("url"), row.get("apply_url")) if u):
            if "linkedin.com" in url:
                time.sleep(1.5)                             # be gentle with LinkedIn
            if ad_closed(session, url):
                store.update(row["uid"], status="skipped", excluded_reason=AD_CLOSED)
                closed += 1
                break
        checked[row["uid"]] = now.isoformat(timespec="seconds")
    open_uids = {r["uid"] for r in store.list(limit=100_000) if r["status"] in ("new", "prepared")}
    checked = {k: v for k, v in checked.items() if k in open_uids}
    try:
        checked_file.parent.mkdir(parents=True, exist_ok=True)
        checked_file.write_text(json.dumps(checked), encoding="utf-8")
    except OSError:
        pass
    if closed:
        log.info("%d jobs say they no longer accept applications and left your list", closed)
    return closed


def close_dead_links(store, session, limit: int = 150, dry_run: bool = False) -> int:
    rows = [r for r in store.list(limit=100_000) if r["status"] in ("new", "prepared") and r.get("apply_url")]
    cache: dict = {}
    closed = 0
    for row in rows[:limit]:
        if is_closed(session, row["apply_url"], cache):
            closed += 1
            if dry_run:
                print(f"closed: {row['uid'][:8]}  {row['company'][:22]:<22} {row['title'][:45]}  {row['apply_url'][:70]}")
            else:
                store.update(row["uid"], status="skipped", excluded_reason=CLOSED)
    if closed and not dry_run:
        log.info("%d jobs have a closed application page and left your list", closed)
    if dry_run:
        print(f"{closed} of {min(len(rows), limit)} open jobs have a closed application page")
    return closed
