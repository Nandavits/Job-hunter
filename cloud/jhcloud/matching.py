"""Per-user matching: score only the jobs this user has not seen yet.

Cost per run = new jobs x users (not all jobs x users), and only jobs that pass the user's
filters are stored, so the matches table grows with what people actually look at.
"""
from __future__ import annotations

import logging

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.config import effective_skills
from jobhunter.dates import age_text
from jobhunter.filters import evaluate, min_years_required, quick_keep
from jobhunter.models import Job as JobRecord

from .models import Job, Match, User

log = logging.getLogger("jhcloud")
BATCH = 2000


def user_search(user: User) -> dict:
    settings = yaml.safe_load(user.settings_yaml or "") or {}
    profile = yaml.safe_load(user.profile_yaml or "") or {}
    search = dict(settings.get("search") or {})
    search["skills"] = effective_skills(search.get("skills") or {}, profile)
    return search


def to_record(row: Job) -> JobRecord:
    return JobRecord(source=row.source, external_id=row.external_id, company=row.company, title=row.title,
                     url=row.url, location=row.location, description=row.description, apply_url=row.apply_url,
                     posted_at=row.posted_at, remote=row.remote,
                     extra={"collected_at": row.collected_at.isoformat() if row.collected_at else ""})


def why(job: JobRecord) -> str:
    parts = []
    if len(job.description) < 100:
        parts.append("No ad text yet: score is a guess")
    else:
        parts.append("You have: " + (", ".join(job.matched_skills[:8]) or "none of your skills named"))
        missing = job.extra.get("missing_skills") or []
        parts.append("Missing: " + (", ".join(missing[:6]) if missing else "nothing"))
        years = min_years_required(job.description)
        parts.append(f"Asks {years}+ years" if years else "Years not stated")
    posted = age_text(job.posted_at)
    if posted:
        parts.append(f"Posted {posted}")
    return " · ".join(parts)


def match_user(db: Session, user_id: int, batch: int = BATCH) -> tuple[int, bool]:
    """Match up to `batch` new jobs for one user. Returns (matches added, more jobs waiting)."""
    user = db.get(User, user_id)
    if user is None:
        return 0, False
    search = user_search(user)
    keep = quick_keep(search)                         # cheap title/location check before the full evaluation
    rows = db.scalars(select(Job).where(Job.id > user.last_matched_job_id).order_by(Job.id).limit(batch)).all()
    ids = [r.id for r in rows]
    already = set(db.scalars(select(Match.job_id).where(Match.user_id == user.id, Match.job_id.in_(ids)))) if ids else set()
    added = 0
    for row in rows:
        if row.id in already:                         # e.g. a job the user applied to, kept through a re-match
            continue
        record = to_record(row)
        if not keep(record):
            continue
        evaluate(record, search)
        if record.excluded_reason:
            continue                                  # not stored: keeps the per-user table small
        db.add(Match(user_id=user.id, job_id=row.id, score=record.score, why=why(record)))
        added += 1
    if rows:
        user.last_matched_job_id = rows[-1].id
    db.commit()
    return added, len(rows) == batch


def rematch_user(db: Session, user_id: int) -> int:
    """After a user changes settings: drop matches they have not acted on, then score every job again."""
    user = db.get(User, user_id)
    for m in db.scalars(select(Match).where(Match.user_id == user_id, Match.status == "new")).all():
        db.delete(m)
    user.last_matched_job_id = 0
    db.commit()
    total, more = 0, True
    while more:
        added, more = match_user(db, user_id)
        total += added
    return total
