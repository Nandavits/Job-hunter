"""Arbeitnow (arbeitnow.com): job board for Germany, many English-speaking roles.

Uses Arbeitnow's free public Job Board API (https://www.arbeitnow.com/api/job-board-api), which needs
no key. Arbeitnow asks API users to link back to arbeitnow.com: every job keeps its arbeitnow.com URL.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import Job, html_to_text
from .base import get, log

API = "https://www.arbeitnow.com/api/job-board-api"


def _date(value) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    return str(value or "")[:10]


def parse(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("data") or []:
        slug = item.get("slug") or item.get("url", "").rstrip("/").split("/")[-1]
        if not slug:
            continue
        location = item.get("location") or ""
        tags = [t for t in (item.get("tags") or []) if isinstance(t, str)]
        jobs.append(Job(
            source="arbeitnow",
            external_id=slug,
            company=(item.get("company_name") or "").strip(),
            title=(item.get("title") or "").strip(),
            url=item.get("url") or f"https://www.arbeitnow.com/jobs/{slug}",
            location=location,
            description=html_to_text(item.get("description")) + ("\nTags: " + ", ".join(tags) if tags else ""),
            posted_at=_date(item.get("created_at")),
            remote=bool(item.get("remote")) or "remote" in location.lower(),
            extra={"job_types": item.get("job_types") or []},
        ))
    return jobs


def fetch(session, cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    url, params = API, {}
    for _ in range(int(cfg.get("max_pages", 5))):
        resp = get(session, url, params=params)
        if resp is None:
            break
        data = resp.json()
        jobs.extend(parse(data))
        next_url = (data.get("links") or {}).get("next")
        if not next_url:
            break
        url, params = next_url, {}
    log.info("%-20s %3d jobs", "Arbeitnow", len(jobs))
    return jobs
