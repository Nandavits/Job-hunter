"""Tables. The split that makes it scale:

  jobs     - SHARED: one row per job ad, collected once for all users
  users    - one row per person (profile, settings, encrypted API key)
  matches  - PER USER: only the jobs that passed that user's filters, with score and status

A user's matching reads only jobs newer than `users.last_matched_job_id`, so each run costs
(new jobs x users), never (all jobs x users).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    profile_yaml: Mapped[str] = mapped_column(Text, default="")
    settings_yaml: Mapped[str] = mapped_column(Text, default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")        # encrypted, never shown back
    last_matched_job_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)          # grows: used as each user's watermark
    uid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[str] = mapped_column(String(10), default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="one_match_per_user_job"),
                      Index("matches_user_status_score", "user_id", "status", "score"),
                      Index("matches_status_created", "status", "created_at"))       # for the clean-up
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="new")    # new, prepared, applied, interview, rejected, offer, skipped
    why: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    applied_at: Mapped[str] = mapped_column(String(10), default="")
    cv_path: Mapped[str] = mapped_column(Text, default="")
    letter_path: Mapped[str] = mapped_column(Text, default="")
    prep_state: Mapped[str] = mapped_column(String(20), default="")   # "", queued, done, failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
