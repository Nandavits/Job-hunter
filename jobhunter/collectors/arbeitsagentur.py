"""Bundesagentur für Arbeit job search (Germany's federal job board).

Uses the public Jobsuche API documented by the bundesAPI project
(https://github.com/bundesAPI/jobsuche-api). It aggregates a large share of German job ads,
including many that also appear on StepStone, Indeed and company sites.
"""
from __future__ import annotations

import base64
from typing import Callable

from ..models import Job, html_to_text
from .base import get, log

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
HEADERS = {"X-API-Key": "jobboerse-jobsuche"}
DETAIL_FIELDS = ("stellenangebotsBeschreibung", "stellenbeschreibung", "beschreibung")


def _location(arbeitsort: dict | None) -> str:
    if not arbeitsort:
        return ""
    return ", ".join(p for p in (arbeitsort.get("ort"), arbeitsort.get("region")) if p)


def parse_search(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("stellenangebote") or []:
        refnr = item.get("refnr") or item.get("referenznummer") or ""
        if not refnr:
            continue
        jobs.append(Job(
            source="arbeitsagentur",
            external_id=refnr,
            company=(item.get("arbeitgeber") or "").strip(),
            title=(item.get("titel") or item.get("beruf") or "").strip(),
            url=f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
            apply_url=item.get("externeUrl") or "",
            location=_location(item.get("arbeitsort")),
            posted_at=(item.get("aktuelleVeroeffentlichungsdatum") or "")[:10],
            extra={"beruf": item.get("beruf", "")},
        ))
    return jobs


def parse_detail(data: dict) -> str:
    for key in DETAIL_FIELDS:
        if data.get(key):
            return html_to_text(data[key])
    return ""


def fetch_detail(session, refnr: str) -> dict:
    encoded = base64.b64encode(refnr.encode()).decode()
    resp = get(session, f"{BASE}/pc/v4/jobdetails/{encoded}", headers=HEADERS)
    return resp.json() if resp is not None else {}


def fetch(session, cfg: dict, keep: Callable[[Job], bool] | None = None, max_details: int = 60) -> list[Job]:
    """Search each query; fetch full descriptions only for jobs that pass the quick `keep` check."""
    results: dict[str, Job] = {}
    for query in cfg.get("queries", []):
        params = {
            "was": query,
            "wo": cfg.get("where", "Berlin"),
            "umkreis": cfg.get("radius_km", 30),
            "angebotsart": 1,  # 1 = employment (not apprenticeships/self-employment)
            "veroeffentlichtseit": cfg.get("published_since_days", 7),
            "size": 100,
            "page": 1,
        }
        resp = get(session, f"{BASE}/pc/v4/jobs", params=params, headers=HEADERS)
        if resp is None:
            log.warning("Arbeitsagentur search failed for '%s'", query)
            continue
        for job in parse_search(resp.json()):
            results.setdefault(job.external_id, job)

        if cfg.get("include_remote"):
            params_remote = dict(params, arbeitszeit="ho")
            params_remote.pop("wo"), params_remote.pop("umkreis")
            resp = get(session, f"{BASE}/pc/v4/jobs", params=params_remote, headers=HEADERS)
            if resp is not None:
                for job in parse_search(resp.json()):
                    job.remote = True
                    results.setdefault(job.external_id, job)

    jobs = [j for j in results.values() if keep is None or keep(j)]
    for job in jobs[:max_details]:
        detail = fetch_detail(session, job.external_id)
        job.description = parse_detail(detail)
        if not job.apply_url:
            job.apply_url = detail.get("allianzpartnerUrl") or detail.get("externeUrl") or ""
    return jobs
