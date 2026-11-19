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
