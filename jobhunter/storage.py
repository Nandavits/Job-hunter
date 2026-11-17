"""Job storage (SQLite by default, PostgreSQL via DATABASE_URL) and the application tracker."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (Boolean, Column, Integer, MetaData, String, Table, Text, create_engine,
                        func, select, update)

from .models import Job

metadata = MetaData()

jobs_table = Table(
    "jobs", metadata,
    Column("uid", String(16), primary_key=True),
    Column("source", Text, nullable=False),
    Column("external_id", Text, nullable=False),
    Column("company", Text),
    Column("title", Text),
    Column("location", Text),
    Column("url", Text),
    Column("apply_url", Text),
    Column("description", Text),
    Column("posted_at", Text),
    Column("remote", Boolean, default=False),
    Column("score", Integer, default=0),
    Column("german_required", Boolean, default=False),
    Column("excluded_reason", Text, default=""),
    Column("matched_skills", Text, default=""),
    Column("collected_at", Text),
    # application tracking
    Column("status", Text, default="new"),   # new | prepared | applied | interview | offer | rejected | skipped
    Column("cv_path", Text, default=""),
    Column("letter_path", Text, default=""),
    Column("applied_at", Text, default=""),
    Column("notes", Text, default=""),
)

STATUSES = ["new", "prepared", "applied", "interview", "offer", "rejected", "skipped"]
TRACKER_COLUMNS = ["uid", "status", "score", "company", "title", "location", "posted_at", "source",
                   "application_url", "matched_skills", "cv_path", "letter_path", "applied_at", "notes"]


class Store:
    def __init__(self, url: str):
        self.engine = create_engine(url, future=True)
        metadata.create_all(self.engine)
        self._upgrade_columns()

    def _upgrade_columns(self) -> None:
        """Databases created by v0.1 had length-limited text columns; widen them (Postgres only)."""
        if self.engine.dialect.name != "postgresql":
            return
        with self.engine.begin() as conn:
            for col in jobs_table.columns:
                if isinstance(col.type, Text):
                    conn.exec_driver_sql(f'ALTER TABLE jobs ALTER COLUMN "{col.name}" TYPE TEXT')

    def save(self, jobs: list[Job]) -> list[str]:
        """Insert jobs not seen before (by uid). Returns uids of newly inserted jobs."""
        new_uids = []
        with self.engine.begin() as conn:
            existing = {r[0] for r in conn.execute(select(jobs_table.c.uid))}
            for job in jobs:
                if job.uid in existing:
                    continue
                row = job.to_row()
                row = {k: v for k, v in row.items() if k in jobs_table.c}
                row["status"] = "skipped" if job.excluded_reason else "new"
                conn.execute(jobs_table.insert().values(**row))
                existing.add(job.uid)
                new_uids.append(job.uid)
        return new_uids

    def get(self, uid: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs_table).where(jobs_table.c.uid.startswith(uid))).mappings().first()
            return dict(row) if row else None

    def list(self, status: str | None = None, limit: int = 50, include_skipped: bool = False) -> list[dict]:
        query = select(jobs_table)
        if status:
            query = query.where(jobs_table.c.status == status)
        elif not include_skipped:
            query = query.where(jobs_table.c.status != "skipped")
        query = query.order_by(jobs_table.c.collected_at.desc(), jobs_table.c.score.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(query).mappings()]

    def update(self, uid: str, **values) -> None:
        if values.get("status") == "applied" and not values.get("applied_at"):
            values["applied_at"] = datetime.now(timezone.utc).date().isoformat()
        with self.engine.begin() as conn:
            conn.execute(update(jobs_table).where(jobs_table.c.uid == uid).values(**values))

    def stats(self) -> dict:
        with self.engine.connect() as conn:
            rows = conn.execute(select(jobs_table.c.status, func.count()).group_by(jobs_table.c.status))
            return {status: count for status, count in rows}

    def export_tracker(self, path: Path) -> Path:
        """Write the tracker CSV (open it in Excel / Google Sheets)."""
        rows = self.list(limit=10_000)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=TRACKER_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                row["application_url"] = row.get("apply_url") or row.get("url")
                writer.writerow(row)
        return path
