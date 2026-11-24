"""Background work, run by RQ workers. The web app only puts tasks on the queue and returns at once.

    ingest_and_fan_out   every 30 min: fetch jobs once, then queue matching for users in groups
    match_users          score new jobs for a group of users
    prepare_documents    tailor a CV + letter for one user and one job (uses that user's own API key)

Add more `rq worker` processes to handle more users; nothing else changes.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from redis import Redis
from rq import Queue
from sqlalchemy import select

from jobhunter.documents import make_documents
from jobhunter.tailor import tailor

from .accounts import profile_of
from .db import init_db, make_engine, make_sessionmaker
from .ingest import ingest
from .matching import match_user
from .models import Job, Match, User
from .security import Keys
from .settings import ServerSettings

log = logging.getLogger("jhcloud")
GROUP = 200          # users per matching task: fewer, larger tasks than one per user


_override = None


@lru_cache(maxsize=1)
def _state_from_env():
    s = ServerSettings.from_env()
    engine = make_engine(s.database_url)
    init_db(engine)
    return s, make_sessionmaker(engine)


def configure(settings: ServerSettings, sessionmaker) -> None:
    """Tests (and the web app) hand in their own settings and database instead of reading the environment."""
    global _override
    _override = (settings, sessionmaker)


def state():
    return _override or _state_from_env()


_queue: Queue | None = None


def queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue("jobhunter", connection=Redis.from_url(state()[0].redis_url))
    return _queue


def set_queue(q: Queue) -> None:
    global _queue
    _queue = q


KEEP_UNTOUCHED_DAYS = 30


def clean_up(db, days: int = KEEP_UNTOUCHED_DAYS) -> int:
    """Delete matches nobody opened or acted on within `days`. Without this the table grows forever
    (the load test showed ~11% of all user x job pairs are kept). Jobs you acted on are never deleted."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = db.execute(delete(Match).where(Match.status == "new", Match.prep_state == "", Match.created_at < cutoff))
    db.commit()
    return result.rowcount or 0


def ingest_and_fan_out() -> dict:
    settings, Session = state()
    with Session() as db:
        new = ingest(db)
        clean_up(db)
        user_ids = list(db.scalars(select(User.id).order_by(User.id)))
        with_gmail = list(db.scalars(select(User.id).where(User.google_refresh_enc != "").order_by(User.id)))
    for i in range(0, len(user_ids), GROUP):
        queue().enqueue(match_users, user_ids[i:i + GROUP], job_timeout=1800)
    for i in range(0, len(with_gmail), GROUP):
        queue().enqueue(check_replies_users, with_gmail[i:i + GROUP], job_timeout=1800)
    return {"new_jobs": new, "users": len(user_ids), "gmail_users": len(with_gmail)}


def check_replies_users(user_ids: list[int]) -> int:
    """Read replies to applications for users who connected Gmail (read-only)."""
    from . import google
    settings, Session = state()
    keys = Keys(settings.secret_key)
    changed = 0
    with Session() as db:
        for uid in user_ids:
            user = db.get(User, uid)
            token = keys.decrypt(user.google_refresh_enc) if user else ""
            if not token:
                continue
            try:
                changed += len(google.check_replies(db, user, token))
            except Exception as exc:                     # one broken account must not stop the others
                log.warning("Gmail replies failed for user %s: %s", uid, exc)
                db.rollback()
    return changed


def match_users(user_ids: list[int]) -> int:
    _, Session = state()
    total = 0
    with Session() as db:
        for uid in user_ids:
            more = True
            while more:
                added, more = match_user(db, uid)
                total += added
    return total


def send_email(to: str, subject: str, body: str) -> None:
    from . import mailer
    mailer.send(to, subject, body)


def prepare_documents(user_id: int, match_id: int) -> str:
    settings, Session = state()
    keys = Keys(settings.secret_key)
    with Session() as db:
        match = db.get(Match, match_id)
        if match is None or match.user_id != user_id:          # a user can only prepare their own matches
            return "not found"
        user, job = db.get(User, user_id), db.get(Job, match.job_id)
        try:
            job_dict = {"company": job.company, "title": job.title, "location": job.location,
                        "description": job.description, "url": job.url}
            profile = profile_of(user)
            t = tailor(profile, job_dict, api_key=keys.decrypt(user.api_key_enc))
            out = settings.storage_dir / str(user_id)
            files = make_documents(profile, t, job_dict, out, make_pdf=False)
            match.cv_path, match.letter_path = str(files.get("cv_docx", "")), str(files.get("letter_docx", ""))
            match.prep_state = "done"
            if match.status == "new":
                match.status = "prepared"
        except Exception as exc:                                 # the user sees "failed" and can try again
            log.warning("Preparing documents failed for match %s: %s", match_id, exc)
            match.prep_state = "failed"
        db.commit()
        return match.prep_state
