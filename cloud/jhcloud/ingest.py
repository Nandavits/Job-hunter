"""Shared ingestion: fetch every source ONCE for all users and store new jobs in the shared table.

This is what makes the design scale: the number of requests to job sites does not depend on
the number of users (1 user or 100,000 users = the same ~15 sources every 30 minutes).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.collectors import collect_all
from jobhunter.pipeline import fill_posted_date

from .models import Job
from .settings import CONFIG

log = logging.getLogger("jhcloud")


def load_sources(path=CONFIG / "sources.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def relevant(cfg: dict):
    words = [w.lower() for w in cfg.get("title_keywords") or []]
    return lambda job: not words or any(w in job.title.lower() for w in words)


def save_jobs(db: Session, records) -> int:
    """Insert jobs not seen before; fill in a description that was missing. Returns how many are new."""
    by_uid = {}
    for r in records:
        by_uid.setdefault(r.uid, r)                  # the same ad from two runs of one source: once
    uids = list(by_uid)
    existing = {}
    for i in range(0, len(uids), 500):              # chunked IN (...) for big batches
        for row in db.scalars(select(Job).where(Job.uid.in_(uids[i:i + 500]))):
            existing[row.uid] = row
    new = 0
    for uid, r in by_uid.items():
        fill_posted_date(r)
        row = existing.get(uid)
        if row is not None:
            if not row.description and r.description:
                row.description = r.description
            continue
        db.add(Job(uid=uid, source=r.source, external_id=str(r.external_id)[:200], company=r.company[:300],
                   title=r.title[:500], location=(r.location or "")[:300], url=r.url, apply_url=r.apply_url,
                   description=r.description, posted_at=(r.posted_at or "")[:10], remote=bool(r.remote)))
        new += 1
    db.commit()
    return new


def ingest(db: Session, cfg: dict | None = None, http=None) -> int:
    cfg = cfg or load_sources()
    keep = relevant(cfg)
    settings = SimpleNamespace(sources=cfg.get("sources") or {}, env=lambda key: "")
    records = [r for r in collect_all(settings, keep=keep, session=http) if keep(r)]
    new = save_jobs(db, records)
    log.info("Ingest: %d jobs fetched, %d new", len(records), new)
    return new
