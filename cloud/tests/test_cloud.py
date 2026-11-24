"""Multi-user version: accounts, isolation between users, matching, the queue, security."""
import re

import yaml
from sqlalchemy import select

from jhcloud import tasks
from jhcloud.ingest import save_jobs
from jhcloud.matching import match_user, rematch_user
from jhcloud.models import Job, Match, User
from jobhunter.models import Job as Record

from conftest import signup

AD = ("We are an English speaking team in Berlin. You build dbt models in SQL and Python and run "
      "Airflow pipelines. You have 2+ years of experience with data modelling. " * 3)


def record(n, title="Data Engineer", location="Berlin", description=AD, company=None):
    return Record(source="test", external_id=str(n), company=company or f"Company {n}", title=title,
                  url=f"https://example.com/{n}", location=location, description=description)


def user_id(server, email):
    with server["Session"]() as db:
        return db.scalar(select(User.id).where(User.email == email))


def csrf(client, path="/settings"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


# ---------- accounts ----------

def test_signup_login_logout(client):
    r = signup(client)
    assert r.status_code == 303 and r.headers["location"] == "/jobs"
    assert client.get("/jobs").status_code == 200
    client.post("/logout")
    client.cookies.clear()
    assert client.get("/jobs", follow_redirects=False).headers["location"] == "/login"
    r = client.post("/login", data={"email": "NAN@example.com ", "password": "a-long-password"}, follow_redirects=False)
    assert r.headers["location"] == "/jobs"


def test_bad_signups_and_logins(client):
    assert "valid email" in client.post("/signup", data={"email": "nope", "password": "a-long-password"}).text
    assert "10 characters" in client.post("/signup", data={"email": "a@b.de", "password": "short"}).text
    signup(client, "a@b.de")
    client.cookies.clear()
    assert "already exists" in client.post("/signup", data={"email": "a@b.de", "password": "a-long-password"}).text
    wrong = client.post("/login", data={"email": "a@b.de", "password": "wrong-password"}).text
    unknown = client.post("/login", data={"email": "nobody@b.de", "password": "wrong-password"}).text
    assert "Wrong email or password" in wrong and "Wrong email or password" in unknown   # no account probing


def test_login_rate_limit(client):
    signup(client, "a@b.de")
    client.cookies.clear()
    for _ in range(10):
        client.post("/login", data={"email": "a@b.de", "password": "wrong-password"})
    blocked = client.post("/login", data={"email": "a@b.de", "password": "a-long-password"})
    assert "Too many failed logins" in blocked.text


def test_passwords_and_keys_are_not_stored_in_plain_text(client, server):
    signup(client)
    client.post("/settings/key", data={"api_key": "sk-ant-secret-1234", "csrf": csrf(client)})
    with server["Session"]() as db:
        user = db.scalar(select(User))
        assert "a-long-password" not in user.password_hash and user.password_hash.startswith("scrypt$")
        assert user.api_key_enc and "sk-ant" not in user.api_key_enc
    assert "ends in ...1234" in client.get("/settings").text and "sk-ant-secret" not in client.get("/settings").text


def test_forms_need_a_csrf_token(client):
    signup(client)
    r = client.post("/settings/key", data={"api_key": "x"})
    assert r.status_code == 400


def test_security_headers_and_health(client):
    r = client.get("/login")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/health").json() == {"database": "ok", "queue": "ok"}


# ---------- shared jobs, per-user matching ----------

def test_jobs_are_stored_once_and_matched_per_user(client, server):
    signup(client, "a@x.de")
    signup(client, "b@x.de")
    a, b = user_id(server, "a@x.de"), user_id(server, "b@x.de")
    with server["Session"]() as db:
        assert save_jobs(db, [record(1), record(2, title="Sales Manager"), record(1)]) == 2   # job 1 sent twice: stored once
    with server["Session"]() as db:
        assert db.query(Job).count() == 2                       # shared table: each ad once
        user_b = db.get(User, b)                                # B only wants Munich
        settings = yaml.safe_load(user_b.settings_yaml)
        settings["search"]["locations"] = ["munich"]
        settings["search"]["anywhere_in_germany"] = False
        user_b.settings_yaml = yaml.safe_dump(settings)
        db.commit()
        assert match_user(db, a) == (1, False)                  # Data Engineer in Berlin: yes; Sales Manager: no
        assert match_user(db, b) == (0, False)                  # Berlin is not Munich
        assert match_user(db, a) == (0, False)                  # watermark: nothing new, nothing re-scored
        save_jobs(db, [record(3)])
        assert match_user(db, a) == (1, False)                  # only the new job is looked at
        m = db.scalar(select(Match).where(Match.user_id == a))
        assert m.score >= 70 and "You have:" in m.why and "Asks 2+ years" in m.why


def test_fan_out_through_the_queue(server, monkeypatch, client):
    for e in ("a@x.de", "b@x.de", "c@x.de"):
        signup(client, e)

    def fake_ingest(db):
        return save_jobs(db, [record(10), record(11)])
    monkeypatch.setattr(tasks, "ingest", fake_ingest)
    result = tasks.ingest_and_fan_out()
    assert result == {"new_jobs": 2, "users": 3}
    with server["Session"]() as db:
        assert db.query(Match).count() == 6                     # 2 jobs x 3 users, matched by queued tasks


def test_rematch_keeps_jobs_you_acted_on(server, client):
    signup(client)
    uid = user_id(server, "nan@example.com")
    with server["Session"]() as db:
        save_jobs(db, [record(1), record(2)])
        match_user(db, uid)
        first = db.scalar(select(Match).where(Match.user_id == uid).order_by(Match.id))
        first.status = "applied"
        db.commit()
        assert rematch_user(db, uid) == 1                       # job 2 again; job 1 is kept as 'applied'
        statuses = sorted(m.status for m in db.scalars(select(Match).where(Match.user_id == uid)))
        assert statuses == ["applied", "new"]


# ---------- isolation between users ----------

def _one_match(server, client, email):
    signup(client, email)
    uid = user_id(server, email)
    with server["Session"]() as db:
        save_jobs(db, [record(abs(hash(email)) % 10000)])
        match_user(db, uid)
        return db.scalar(select(Match.id).where(Match.user_id == uid))


def test_users_cannot_see_or_change_each_others_jobs(server, client):
    from fastapi.testclient import TestClient
    mine = _one_match(server, client, "a@x.de")
    other = TestClient(server["app"])
    signup(other, "b@x.de")
    assert other.get(f"/jobs/{mine}").status_code == 404
    token = csrf(other)
    assert other.post(f"/jobs/{mine}/status", data={"status": "rejected", "csrf": token}).status_code == 404
    assert other.get(f"/files/{mine}/cv").status_code == 404
    with server["Session"]() as db:
        assert db.get(Match, mine).status == "new"


def test_prepare_documents_without_a_key_and_download(server, client):
    mid = _one_match(server, client, "a@x.de")
    with server["Session"]() as db:                             # a real profile instead of the example
        user = db.scalar(select(User))
        profile = yaml.safe_load(user.profile_yaml)
        profile["personal"]["name"] = "Nan Example"
        user.profile_yaml = yaml.safe_dump(profile)
        db.commit()
    r = client.post(f"/jobs/{mid}/prepare", data={"csrf": csrf(client, f"/jobs/{mid}")})
    assert r.status_code == 200 or r.status_code == 303
    page = client.get(f"/jobs/{mid}").text
    assert "/files/" in page and "Cover letter" in page
    cv = client.get(f"/files/{mid}/cv")
    assert cv.status_code == 200 and cv.content[:2] == b"PK"   # a real .docx (zip)
    with server["Session"]() as db:
        assert db.get(Match, mid).status == "prepared"


def test_html_in_job_ads_is_escaped(server, client):
    signup(client)
    uid = user_id(server, "nan@example.com")
    with server["Session"]() as db:
        save_jobs(db, [record(7, title="Data Engineer <script>alert(1)</script>")])
        match_user(db, uid)
    page = client.get("/jobs").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_settings_are_checked_before_saving(client):
    signup(client)
    token = csrf(client)
    r = client.post("/settings", data={"profile": "personal: [broken", "search": "search: {}", "csrf": token})
    assert "Not saved" in r.text
