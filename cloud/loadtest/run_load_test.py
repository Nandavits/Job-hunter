"""Load test for Job Hunter Cloud: realistic users and jobs, real PostgreSQL, real Redis, real RQ workers.

    python loadtest/run_load_test.py --users 5000 --jobs 1000 --workers 2

Phase 1  create users (each with different skills, places, limits) and shared jobs
Phase 2  matching: queue all users, let N `rq worker` processes match them, time it
Phase 3  website: many simultaneous people opening their job list, measure response times
Results go to loadtest/results.json and are printed as a table.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent)]

import httpx  # noqa: E402
import yaml  # noqa: E402
from redis import Redis  # noqa: E402
from rq import Queue  # noqa: E402
from sqlalchemy import delete, func, insert, select  # noqa: E402

from jhcloud import tasks  # noqa: E402
from jhcloud.db import init_db, make_engine, make_sessionmaker  # noqa: E402
from jhcloud.models import Job, Match, User  # noqa: E402
from jhcloud.security import Keys, hash_password  # noqa: E402
from jhcloud.settings import ServerSettings, default_text  # noqa: E402

TOOLS = ["SQL", "Python", "dbt", "Airflow", "Docker", "Spark", "Kafka", "Snowflake", "BigQuery", "Power BI", "Tableau",
         "Looker", "AWS", "Azure", "Kubernetes", "Databricks", "Terraform", "Excel", "PostgreSQL", "Scala"]
TITLES = ["Data Engineer", "Junior Data Engineer", "Analytics Engineer", "Data Analyst", "BI Analyst", "BI Developer",
          "Senior Data Engineer", "Data Platform Engineer", "Sales Manager", "Account Executive", "Nurse",
          "Datenanalyst (m/w/d)", "Werkstudent Data", "AI Engineer", "Software Engineer"]
PLACES = ["Berlin", "Munich", "Hamburg", "Cologne", "Frankfurt", "Remote, Germany", "Paris", "London", "Hyderabad, India",
          "Leipzig", "Stuttgart"]


def job_text(rng: random.Random) -> str:
    tools = rng.sample(TOOLS, rng.randint(3, 7))
    years = rng.choice(["", "1+ years", "2+ years", "3-5 years", "5+ years"])
    parts = [f"You build pipelines and reports with {', '.join(tools[:-1])} and {tools[-1]}.",
             "You work with stakeholders and improve data quality and data models."]
    if years:
        parts.append(f"You have {years} of experience in a similar role.")
    if rng.random() < 0.15:
        parts.append("Sie sprechen fließend Deutsch und arbeiten gern im Team mit unseren Kunden in ganz Deutschland.")
    else:
        parts.append("We are an English speaking team; German is a plus but not required.")
    return " ".join(parts * 3)


def user_settings(rng: random.Random, base_search: dict) -> str:
    s = dict(base_search)
    s["locations"] = rng.sample(["berlin", "munich", "hamburg", "remote", "germany", "cologne"], rng.randint(1, 4))
    s["anywhere_in_germany"] = rng.random() < 0.6
    s["max_years_experience"] = rng.choice([1, 2, 3, 5])
    s["min_score"] = rng.choice([55, 60, 70])
    s["also_countries"] = ["india"] if rng.random() < 0.2 else []
    return yaml.safe_dump({"search": s})


def user_profile(rng: random.Random, base_profile: dict) -> str:
    p = json.loads(json.dumps(base_profile))
    p["personal"]["name"] = f"Test User {rng.randint(1, 10**6)}"
    p["skills"] = {"Tools": rng.sample(TOOLS, rng.randint(4, 9))}
    return yaml.safe_dump(p)


def phase_setup(Session, users: int, jobs: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    base_search = (yaml.safe_load(default_text("user-settings.default.yaml")) or {})["search"]
    base_profile = yaml.safe_load(default_text("profile.default.yaml"))
    one_hash = hash_password("load-test-password")              # hashing is slow on purpose: reuse one hash
    with Session() as db:
        db.execute(delete(Match))
        db.execute(delete(Job))
        db.execute(delete(User))
        db.commit()
        rows = [{"email": f"user{i}@load.test", "password_hash": one_hash, "profile_yaml": user_profile(rng, base_profile),
                 "settings_yaml": user_settings(rng, base_search), "api_key_enc": "", "last_matched_job_id": 0}
                for i in range(users)]
        for i in range(0, len(rows), 1000):
            db.execute(insert(User), rows[i:i + 1000])
        jobrows = [{"uid": f"load{i:08d}", "source": "load", "external_id": str(i), "company": f"Company {i % 400}",
                    "title": rng.choice(TITLES), "location": rng.choice(PLACES), "url": f"https://example.com/{i}",
                    "apply_url": "", "description": job_text(rng), "posted_at": "2026-11-23", "remote": False}
                   for i in range(jobs)]
        db.execute(insert(Job), jobrows)
        db.commit()


def phase_matching(Session, redis_url: str, workers: int, env: dict) -> dict:
    q = Queue("jobhunter", connection=Redis.from_url(redis_url))
    q.empty()
    with Session() as db:
        ids = list(db.scalars(select(User.id).order_by(User.id)))
        n_jobs = db.scalar(select(func.count()).select_from(Job))
    for i in range(0, len(ids), tasks.GROUP):
        q.enqueue("jhcloud.tasks.match_users", ids[i:i + tasks.GROUP], job_timeout=3600)
    start = time.perf_counter()
    procs = [subprocess.Popen([sys.executable, "-m", "rq.cli", "worker", "jobhunter", "--burst", "--url", redis_url,
                               "--quiet"], env=env, cwd=str(HERE.parent)) for _ in range(workers)]
    for p in procs:
        p.wait()
    seconds = time.perf_counter() - start
    with Session() as db:
        matches = db.scalar(select(func.count()).select_from(Match))
    evaluations = len(ids) * n_jobs
    return {"users": len(ids), "jobs": n_jobs, "workers": workers, "seconds": round(seconds, 1),
            "evaluations": evaluations, "evaluations_per_second": round(evaluations / seconds),
            "matches_stored": matches, "share_stored": round(matches / evaluations, 4)}


async def _hammer(base: str, cookies: list[str], concurrency: int, requests: int) -> dict:
    latencies, errors, busy = [], 0, 0
    kinds: dict = {}
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base, timeout=60, limits=limits) as client:
        async def one(i: int):
            nonlocal errors
            nonlocal busy
            async with sem:
                t = time.perf_counter()
                try:
                    r = await client.get("/jobs", cookies={"jh_session": cookies[i % len(cookies)]})
                    if r.status_code == 503:
                        busy += 1                     # told "busy, try again" at once: handled, not broken
                    elif r.status_code != 200:
                        errors += 1
                        kinds[str(r.status_code)] = kinds.get(str(r.status_code), 0) + 1
                except httpx.HTTPError as exc:
                    errors += 1
                    kinds[type(exc).__name__] = kinds.get(type(exc).__name__, 0) + 1
                latencies.append(time.perf_counter() - t)
        start = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(requests)))
        wall = time.perf_counter() - start
    latencies.sort()
    pct = lambda p: round(1000 * latencies[min(len(latencies) - 1, int(p * len(latencies)))])  # noqa: E731
    return {"concurrent_users": concurrency, "requests": requests, "errors": errors, "busy_503": busy, "error_kinds": kinds,
            "requests_per_second": round(requests / wall), "p50_ms": pct(0.50), "p95_ms": pct(0.95),
            "p99_ms": pct(0.99), "mean_ms": round(1000 * statistics.mean(latencies))}


def phase_web(Session, settings: ServerSettings, env: dict, web_workers: int, levels: list[int],
              limit_concurrency: int = 0) -> list[dict]:
    keys = Keys(settings.secret_key)
    with Session() as db:
        ids = list(db.scalars(select(User.id)))
    cookies = [keys.session_token(i) for i in ids]          # logged-in sessions, as after a real login
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "jhcloud.web:app_from_env", "--factory",
                               "--port", "8765", "--workers", str(web_workers), "--log-level", "warning"]
                              + (["--limit-concurrency", str(limit_concurrency)] if limit_concurrency else []),
                              env=env, cwd=str(HERE.parent))
    try:
        for _ in range(60):
            try:
                if httpx.get("http://127.0.0.1:8765/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        results = []
        for level in levels:
            results.append(asyncio.run(_hammer("http://127.0.0.1:8765", cookies, level, max(400, level * 8))))
        return results
    finally:
        server.terminate()
        server.wait()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=5000)
    ap.add_argument("--jobs", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--web-workers", type=int, default=2)
    ap.add_argument("--levels", default="10,50,100,200")
    ap.add_argument("--web-only", action="store_true", help="reuse the data of the last run")
    ap.add_argument("--limit-concurrency", type=int, default=0)
    args = ap.parse_args()
    settings = ServerSettings.from_env()
    env = {**os.environ, "PYTHONPATH": f"{HERE.parent}{os.pathsep}{HERE.parent.parent}"}
    engine = make_engine(settings.database_url)
    init_db(engine)
    Session = make_sessionmaker(engine)

    previous = json.loads((HERE / "results.json").read_text()) if (HERE / "results.json").exists() else {}
    if args.web_only:
        setup_s, matching = previous.get("setup_seconds"), previous.get("matching")
    else:
        t = time.perf_counter()
        phase_setup(Session, args.users, args.jobs)
        setup_s = round(time.perf_counter() - t, 1)
        matching = phase_matching(Session, settings.redis_url, args.workers, env)
    web = phase_web(Session, settings, env, args.web_workers, [int(x) for x in args.levels.split(",")],
                    args.limit_concurrency)
    out = {"machine_cpus": os.cpu_count(), "setup_seconds": setup_s, "matching": matching, "web": web,
           "web_before_fix": previous.get("web_before_fix") or (previous.get("web") if args.web_only else None)}
    (HERE / "results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
