"""Connect Gmail with Google, and reading replies through the Gmail API (Google is faked here)."""
import base64
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from jhcloud import google, tasks
from jhcloud.ingest import save_jobs
from jhcloud.matching import match_user
from jhcloud.models import Match, User
from jobhunter.models import Job as Record

from conftest import signup

AD = "English speaking team in Berlin. SQL, Python, dbt and Airflow. 2+ years of experience. " * 4
MOSS_NO = ("Thank you for your interest in the Applied AI Engineer position. After careful consideration, "
           "we will not be moving forward with your application.")


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(mid, sender, subject, body):
    return {"id": mid, "payload": {"mimeType": "multipart/alternative",
                                   "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
                                   "parts": [{"mimeType": "text/plain", "body": {"data": b64(body)}}]}}


class FakeGoogle:
    def __init__(self, messages=()):
        self.messages = {m["id"]: m for m in messages}
        self.posts, self.gets = [], []

    def post(self, url, data=None, timeout=None):
        self.posts.append((url, data))
        payload = {}
        if url == google.TOKEN_URL and data.get("grant_type") == "authorization_code":
            claims = b64(json.dumps({"email": "nan@gmail.com"}))
            payload = {"access_token": "at", "refresh_token": "rt-secret", "id_token": f"x.{claims}.y"}
        elif url == google.TOKEN_URL:
            payload = {"access_token": "at-2"}
        return Resp(payload)

    def get(self, url, headers=None, params=None, timeout=None):
        self.gets.append((url, params))
        assert headers["Authorization"] == "Bearer at-2"
        if url.endswith("/messages"):
            return Resp({"messages": [{"id": i} for i in self.messages]})
        return Resp(self.messages[url.rsplit("/", 1)[1]])


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")


def csrf(client):
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/settings").text).group(1)


def connect(client, monkeypatch, fake):
    monkeypatch.setattr(google, "http", fake)
    r = client.post("/google/connect", data={"csrf": csrf(client)}, follow_redirects=False)
    url = urlparse(r.headers["location"])
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com" and "gmail.readonly" in q["scope"][0]
    assert "gmail.send" not in q["scope"][0] and q["code_challenge_method"] == ["S256"]
    return client.get(f"/google/callback?state={q['state'][0]}&code=abc", follow_redirects=False), q


def test_connect_gmail_stores_only_an_encrypted_token(client, server, google_env, monkeypatch):
    signup(client)
    fake = FakeGoogle()
    r, q = connect(client, monkeypatch, fake)
    assert r.headers["location"] == "/settings"
    assert fake.posts[0][1]["code_verifier"]                           # PKCE sent to Google
    with server["Session"]() as db:
        user = db.scalar(select(User))
        assert user.google_email == "nan@gmail.com" and user.google_refresh_enc and "rt-secret" not in user.google_refresh_enc
    assert "Connected: nan@gmail.com" in client.get("/settings").text
    client.post("/google/disconnect", data={"csrf": csrf(client)})
    assert fake.posts[-1][0] == google.REVOKE_URL                     # access revoked at Google too
    with server["Session"]() as db:
        assert db.scalar(select(User)).google_refresh_enc == ""


def test_a_callback_for_another_user_is_refused(client, server, google_env, monkeypatch):
    from fastapi.testclient import TestClient
    signup(client, "a@x.de")
    fake = FakeGoogle()
    monkeypatch.setattr(google, "http", fake)
    r = client.post("/google/connect", data={"csrf": csrf(client)}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    attacker = TestClient(server["app"])
    signup(attacker, "b@x.de")
    attacker.get(f"/google/callback?state={state}&code=abc")           # B tries to use A's sign-in
    with server["Session"]() as db:
        assert all(u.google_refresh_enc == "" for u in db.scalars(select(User)))
    assert fake.posts == []


def test_replies_are_read_and_statuses_updated(client, server, google_env, monkeypatch):
    signup(client)
    with server["Session"]() as db:
        uid = db.scalar(select(User.id))
        save_jobs(db, [Record(source="t", external_id="1", company="Moss", title="Data Engineer",
                              url="https://x/1", location="Berlin", description=AD),
                       Record(source="t", external_id="2", company="Statista", title="Data Engineer",
                              url="https://x/2", location="Berlin", description=AD)])
        match_user(db, uid)
        for m in db.scalars(select(Match)):
            m.status = "applied"
        db.commit()
    fake = FakeGoogle([gmail_message("m1", "Moss Talent <careers@getmoss.com>", "Moss - Application Update", MOSS_NO),
                       gmail_message("m2", "jobalerts-noreply@linkedin.com", "Moss is hiring", "unfortunately")])
    connect(client, monkeypatch, fake)
    assert tasks.check_replies_users([uid]) == 1
    with server["Session"]() as db:
        moss, statista = db.scalars(select(Match).order_by(Match.id)).all()
        assert moss.status == "rejected" and "rejection" in moss.notes
        assert statista.status == "applied"
    before = len(fake.gets)
    assert tasks.check_replies_users([uid]) == 0                     # already read: not again
    assert [url.rsplit("/", 1)[1] for url, _ in fake.gets[before:]] == ["messages"]   # only the list, no email opened
