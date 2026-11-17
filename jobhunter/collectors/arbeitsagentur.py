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


SEARCH_PATHS = ["/pc/v6/jobs", "/pc/v4/jobs", "/pc/v4/app/jobs"]


def _job_list(data: dict) -> list[dict]:
    """The list key has changed between API versions; find the list of job dicts."""
    for key in ("stellenangebote", "stellenAngebote", "jobs", "ergebnisse", "content"):
        if isinstance(data.get(key), list):
            return data[key]
    for value in data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def _first(item: dict, *keys) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("ort")
        if value:
            return str(value).strip()
    return ""


def parse_search(data: dict) -> list[Job]:
    jobs = []
    for item in _job_list(data):
        refnr = _first(item, "refnr", "referenznummer", "refNr")
        if not refnr:
            continue
        jobs.append(Job(
            source="arbeitsagentur",
            external_id=refnr,
            company=_first(item, "arbeitgeber", "arbeitgeberName", "firma"),
            title=_first(item, "titel", "stellenangebotsTitel", "stellentitel", "beruf"),
            url=f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
            apply_url=item.get("externeUrl") or "",
            location=_location(item.get("arbeitsort") or (item.get("arbeitsorte") or [None])[0]),
            posted_at=_first(item, "aktuelleVeroeffentlichungsdatum", "veroeffentlichungsdatum",
                             "eintrittsdatum")[:10],
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


def search(session, params: dict) -> list[Job]:
    """Try the API versions newest-first; the agency has retired old paths before."""
    for path in SEARCH_PATHS:
        resp = get(session, f"{BASE}{path}", params=params, headers=HEADERS)
        if resp is None:
            continue
        data = resp.json()
        jobs = parse_search(data)
        if jobs or _job_list(data) == [] and "maxErgebnisse" in data:
            return jobs
        log.warning("Arbeitsagentur %s: unexpected response keys %s", path, list(data)[:10])
    return []


def fetch(session, cfg: dict, keep: Callable[[Job], bool] | None = None, max_details: int = 120) -> list[Job]:
    """Search each query; fetch full descriptions only for jobs that pass the quick `keep` check."""
    results: dict[str, Job] = {}
    for query in cfg.get("queries", []):
        params = {
            "was": query,
            "angebotsart": 1,  # 1 = employment (not apprenticeships/self-employment)
            "veroeffentlichtseit": cfg.get("published_since_days", 7),
            "size": 100,
            "page": 1,
        }
        if cfg.get("where"):  # empty = all of Germany
            params.update(wo=cfg["where"], umkreis=cfg.get("radius_km", 30))
        found = search(session, params)
        if not found:
            log.warning("Arbeitsagentur: no results for '%s'", query)
        for job in found:
            results.setdefault(job.external_id, job)

        if cfg.get("include_remote"):
            params_remote = dict(params, arbeitszeit="ho")
            params_remote.pop("wo", None), params_remote.pop("umkreis", None)
            for job in search(session, params_remote):
                job.remote = True
                results.setdefault(job.external_id, job)

    jobs = [j for j in results.values() if keep is None or keep(j)]
    for job in jobs[:max_details]:
        detail = fetch_detail(session, job.external_id)
        job.description = parse_detail(detail)
        if not job.apply_url:
            job.apply_url = detail.get("allianzpartnerUrl") or detail.get("externeUrl") or ""
    return jobs
