"""Try the website without Docker, Redis or internet:  python -m jhcloud.demo  -> http://127.0.0.1:8000

Uses SQLite and an in-memory queue, adds a few sample jobs and a demo account:
    demo@example.com  /  demo-password-123
"""
from __future__ import annotations

import os
import secrets

import fakeredis
import uvicorn
from rq import Queue

from jobhunter.models import Job as Record

from . import tasks
from .accounts import sign_up
from .db import init_db, make_engine, make_sessionmaker
from .ingest import save_jobs
from .matching import match_user
from .settings import ServerSettings
from .web import create_app

SAMPLES = [
    ("Statista", "Analytics Engineer (m/f/d)", "Berlin",
     "You build dbt models in SQL on Postgres and Airflow pipelines. 2+ years of experience. English speaking team."),
    ("Covestro", "Data Engineer (m/f/d)", "Leverkusen",
     "Python and SQL pipelines with Airflow and Docker. Data quality and modelling. Our team works in English."),
    ("Redcare Pharmacy", "Data Analyst - Procurement", "Cologne",
     "Power BI dashboards, SQL, Excel and Databricks. 1-2 years of experience. English is our working language."),
    ("Acme Cloud", "Senior Data Engineer", "Berlin", "7+ years of experience with Spark and Scala."),
    ("Beispiel GmbH", "Datenanalyst (m/w/d)", "Hamburg", "Sie analysieren Daten mit SQL. Sehr gute Deutschkenntnisse."),
]


def main() -> None:
    settings = ServerSettings(database_url="sqlite:///demo.db", redis_url="redis://unused",
                              secret_key=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
                              storage_dir=ServerSettings.from_env().storage_dir, ingest_every_minutes=30,
                              secure_cookies=False)
    engine = make_engine(settings.database_url)
    init_db(engine)
    Session = make_sessionmaker(engine)
    redis = fakeredis.FakeRedis()
    tasks.configure(settings, Session)
    tasks.set_queue(Queue("jobhunter", connection=redis, is_async=False))
    with Session() as db:
        save_jobs(db, [Record(source="demo", external_id=str(i), company=c, title=t, location=loc, url="https://example.com",
                              description=(d + " ") * 4, posted_at="2026-11-23") for i, (c, t, loc, d) in enumerate(SAMPLES)])
        try:
            user = sign_up(db, "demo@example.com", "demo-password-123")
            match_user(db, user.id)
        except ValueError:
            pass                                                 # demo account exists already
    uvicorn.run(create_app(settings, redis=redis, sessionmaker=Session), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
