"""End-to-end check of a running server:  python -m jhcloud.smoke [base_url]

Signs up a throw-away account, opens the pages, checks the shared job table and the queue.
Prints one line per check and exits with an error code if anything failed.
"""
from __future__ import annotations

import re
import secrets
import sys

import httpx
from sqlalchemy import func, select

from .db import make_engine, make_sessionmaker
from .models import Job, Match, User
from .settings import ServerSettings


def main(base: str = "http://127.0.0.1:8000") -> int:
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    c = httpx.Client(base_url=base, timeout=30, follow_redirects=True)
    health = c.get("/health")
    check("health", health.status_code == 200, health.text)
    email = f"smoke-{secrets.token_hex(4)}@example.com"
    r = c.post("/signup", data={"email": email, "password": "smoke-test-password"})
    check("sign up + log in", r.status_code == 200 and "Your jobs" in r.text)
    settings_page = c.get("/settings")
    check("settings page", settings_page.status_code == 200 and "Your profile" in settings_page.text)
    token = re.search(r'name="csrf" value="([^"]+)"', settings_page.text)
    r = c.post("/settings/key", data={"api_key": "", "csrf": token.group(1) if token else ""})
    check("form with CSRF token", r.status_code == 200)
    r = c.post("/settings/key", data={"api_key": "x"}, follow_redirects=False)
    check("form without CSRF token is refused", r.status_code == 400)
    import time
    from .matching import match_user
    time.sleep(3)                                   # the worker matches the new account against existing jobs
    with make_sessionmaker(make_engine(ServerSettings.from_env().database_url))() as db:
        uid = db.scalar(select(User.id).where(User.email == email))
        waiting = db.scalar(select(func.count()).select_from(Job).where(Job.id > db.get(User, uid).last_matched_job_id))
    check("new account was matched against existing jobs", waiting == 0, f"{waiting} jobs still waiting")
    c.post("/logout")
    c.cookies.clear()
    check("logged out sees login page", "Log in" in c.get("/jobs").text)

    s = ServerSettings.from_env()
    with make_sessionmaker(make_engine(s.database_url))() as db:
        jobs = db.scalar(select(func.count()).select_from(Job))
        users = db.scalar(select(func.count()).select_from(User))
        matches = db.scalar(select(func.count()).select_from(Match))
        sources = dict(db.execute(select(Job.source, func.count()).group_by(Job.source)).all())
        print(f"INFO  shared jobs: {jobs}  users: {users}  matches: {matches}")
        print(f"INFO  jobs per source: {sources}")
        db.execute(User.__table__.delete().where(User.email == email))       # remove the throw-away account
        db.commit()
    print("ALL PASSED" if ok else "SOMETHING FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
