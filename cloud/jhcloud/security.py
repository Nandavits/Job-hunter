"""Passwords, sessions, CSRF tokens and encrypted API keys. Standard library + itsdangerous + cryptography."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_DAYS = 14
_N, _R, _P = 2 ** 14, 8, 1            # scrypt cost: ~50 ms per hash, slows down password guessing


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(test, base64.b64decode(digest))   # constant time: no timing leaks


def password_problem(password: str) -> str:
    if len(password) < 10:
        return "Use at least 10 characters."
    if password.lower() in {"password12", "1234567890", "qwertzuiop", "qwertyuiop"}:
        return "That password is too common."
    return ""


class Keys:
    """Everything derived from the one server secret (SECRET_KEY in .env)."""

    def __init__(self, secret: str):
        if len(secret) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters (it signs logins and encrypts API keys).")
        self.sessions = URLSafeTimedSerializer(secret, salt="session")
        self._csrf = URLSafeTimedSerializer(secret, salt="csrf")
        fernet_key = base64.urlsafe_b64encode(hashlib.sha256(("fernet:" + secret).encode()).digest())
        self._fernet = Fernet(fernet_key)

    # sessions: a signed cookie holding the user id; it cannot be forged without SECRET_KEY
    def session_token(self, user_id: int) -> str:
        return self.sessions.dumps({"u": user_id, "n": secrets.token_hex(4)})

    def user_from_session(self, token: str | None) -> int | None:
        if not token:
            return None
        try:
            return int(self.sessions.loads(token, max_age=SESSION_DAYS * 86400)["u"])
        except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
            return None

    # CSRF: every form carries a token bound to the logged-in user
    def csrf_token(self, user_id: int) -> str:
        return self._csrf.dumps(user_id)

    def csrf_ok(self, token: str | None, user_id: int) -> bool:
        try:
            return token is not None and self._csrf.loads(token, max_age=SESSION_DAYS * 86400) == user_id
        except (BadSignature, SignatureExpired):
            return False

    # API keys are stored encrypted; a database leak alone does not reveal them
    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode()).decode() if text else ""

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return ""
