"""Job collectors. Each returns a list of normalised `Job` objects."""
from __future__ import annotations

from typing import Callable

from ..models import Job
from . import arbeitsagentur, ats, email_alerts
from .base import log, make_session


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
        jobs.extend(found)

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
            jobs.extend(email_alerts.fetch(mail, settings.env("GMAIL_ADDRESS"),
                                           settings.env("GMAIL_APP_PASSWORD")))
        except Exception as exc:
            log.warning("Email alerts failed: %s", exc)

    return jobs
