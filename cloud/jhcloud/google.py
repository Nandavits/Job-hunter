"""'Connect Gmail' with Google sign-in (OAuth 2.0), and reading replies to applications through the Gmail API.

- Scope: gmail.readonly only. Job Hunter can never send, change or delete an email.
- The refresh token is stored encrypted; users can disconnect at any time (the token is revoked at Google).
- PKCE + a signed `state` protect the sign-in against forged callbacks.
- Google rule: gmail.readonly is a "restricted" scope. Free in Testing mode for up to 100 test users;
  more users need Google's verification and a yearly third-party security assessment.

Reply detection reuses the desktop rules (jobhunter/inbox.py), so both versions behave the same.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from datetime import date
from urllib.parse import urlencode

import requests
from sqlalchemy import select

from jobhunter.inbox import NOT_A_REPLY, apply_reply, classify, match_job

from .models import Job, Match, User

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = "openid email https://www.googleapis.com/auth/gmail.readonly"
http = requests.Session()                      # tests replace this


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def consent_url(redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def exchange_code(code: str, redirect_uri: str, verifier: str) -> dict:
    r = http.post(TOKEN_URL, data={"code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"],
                                   "client_secret": os.environ["GOOGLE_CLIENT_SECRET"], "redirect_uri": redirect_uri,
                                   "grant_type": "authorization_code", "code_verifier": verifier}, timeout=20)
    r.raise_for_status()
    return r.json()


def access_token(refresh_token: str) -> str:
    r = http.post(TOKEN_URL, data={"refresh_token": refresh_token, "client_id": os.environ["GOOGLE_CLIENT_ID"],
                                   "client_secret": os.environ["GOOGLE_CLIENT_SECRET"], "grant_type": "refresh_token"},
                  timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def revoke(token: str) -> None:
    try:
        http.post(REVOKE_URL, data={"token": token}, timeout=10)
    except requests.RequestException:
        pass                                    # the token is deleted on our side anyway


def email_of(id_token: str) -> str:
    """The Google address from the id_token we just received over TLS from Google (no signature check needed)."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")
    except (IndexError, ValueError):
        return ""


def _header(msg: dict, name: str) -> str:
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"].lower() == name.lower()), "")


def _body(part: dict) -> str:
    """Plain text of a Gmail API message (prefers text/plain, falls back to stripped HTML)."""
    plain, html = [], []

    def walk(p):
        data = (p.get("body") or {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if p.get("mimeType") == "text/plain" else html if p.get("mimeType") == "text/html" else []).append(text)
        for child in p.get("parts") or []:
            walk(child)
    walk(part)
    if plain:
        return "\n".join(plain)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", " ".join(html)))


class _MatchStore:
    """Lets the desktop apply_reply() update a Match row."""
    def __init__(self, db):
        self.db = db

    def update(self, uid, **values):
        match = self.db.get(Match, int(uid))
        for key, value in values.items():
            setattr(match, key, value)


def check_replies(db, user: User, refresh_token: str, days: int = 14, limit: int = 100) -> list[str]:
    applied = db.execute(select(Match, Job).join(Job, Job.id == Match.job_id)
                         .where(Match.user_id == user.id, Match.status.in_(["applied", "interview"]))).all()
    if not applied:
        return []
    jobs = [{"uid": m.id, "company": j.company, "title": j.title, "status": m.status, "notes": m.notes,
             "applied_at": m.applied_at} for m, j in applied]
    seen = set(json.loads(user.gmail_seen or "[]"))
    headers = {"Authorization": f"Bearer {access_token(refresh_token)}"}
    listing = http.get(f"{GMAIL}/messages", headers=headers, timeout=20,
                       params={"q": f"newer_than:{days}d -category:promotions -category:social", "maxResults": limit})
    listing.raise_for_status()
    changes = []
    for ref in listing.json().get("messages", []):
        if ref["id"] in seen:
            continue
        msg = http.get(f"{GMAIL}/messages/{ref['id']}", headers=headers, params={"format": "full"}, timeout=20).json()
        sender, subject = _header(msg, "From"), _header(msg, "Subject")
        if NOT_A_REPLY.search(sender):
            seen.add(ref["id"])
            continue
        body = _body(msg.get("payload", {}))
        kind = classify(f"{subject}\n{body[:4000]}")
        if not kind:
            seen.add(ref["id"])
            continue
        job = match_job(sender, subject, body, jobs)
        if not job:
            continue
        seen.add(ref["id"])
        changed = apply_reply(_MatchStore(db), job, kind, subject, date.today().isoformat())
        if changed:
            changes.append(f"{job['company']} - {job['title']}: {changed}")
    user.gmail_seen = json.dumps(sorted(seen)[-3000:])
    db.commit()
    return changes
