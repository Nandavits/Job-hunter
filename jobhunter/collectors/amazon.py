"""Amazon (amazon.jobs): reads the same JSON search its careers site uses, for jobs in Germany."""
from __future__ import annotations

from ..models import Job, html_to_text
from .base import get, log

SEARCH = "https://www.amazon.jobs/en/search.json"


def parse(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs") or []:
        path = item.get("job_path") or ""
        description = "\n".join(html_to_text(item.get(k)) for k in
                                ("description", "basic_qualifications", "preferred_qualifications") if item.get(k))
        jobs.append(Job(
            source="amazon", external_id=str(item.get("id_icims") or item.get("id") or path),
            company=item.get("company_name") or "Amazon", title=(item.get("title") or "").strip(),
            url=f"https://www.amazon.jobs{path}" if path.startswith("/") else path,
            apply_url=item.get("url_next_step") or "",
            location=item.get("normalized_location") or item.get("location") or "",
            description=description, posted_at=str(item.get("posted_date") or ""),
        ))
    return jobs


def fetch(session, cfg: dict) -> list[Job]:
    found: dict[str, Job] = {}
    for query in cfg.get("queries") or ["data engineer", "data analyst", "business intelligence"]:
        resp = get(session, SEARCH, params={"base_query": query, "country": "DEU", "result_limit": 100,
                                            "sort": "recent"})
        if resp is None:
            log.warning("Amazon jobs: search not available")
            break
        for job in parse(resp.json()):
            found.setdefault(job.external_id, job)
    log.info("%-20s %3d jobs", "Amazon", len(found))
    return list(found.values())
