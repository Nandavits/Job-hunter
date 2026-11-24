"""Sign-up, login and per-user settings."""
from __future__ import annotations

import re

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.checksettings import check_text

from .models import User
from .security import Keys, hash_password, password_problem, verify_password
from .settings import default_text

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,}$", re.I)


class AccountError(ValueError):
    pass


def sign_up(db: Session, email: str, password: str) -> User:
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise AccountError("Please enter a valid email address.")
    problem = password_problem(password)
    if problem:
        raise AccountError(problem)
    if db.scalar(select(User.id).where(User.email == email)):
        raise AccountError("An account with this email already exists.")
    user = User(email=email, password_hash=hash_password(password),
                profile_yaml=default_text("profile.default.yaml"),
                settings_yaml=default_text("user-settings.default.yaml"))
    db.add(user)
    db.commit()
    return user


def log_in(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not verify_password(password, user.password_hash):
        raise AccountError("Wrong email or password.")      # same message: does not reveal which accounts exist
    return user


def save_settings(db: Session, user: User, profile_text: str, settings_text: str) -> list[str]:
    """Check both YAML texts with the same checker as the desktop version; save only if both are fine."""
    problems = []
    # job sources are server-wide here, so the desktop checker's "sources" rule is satisfied for the user
    server_sources = "\nsources:\n  shared_by_server: {}\n" if "\nsources:" not in "\n" + settings_text else ""
    for name, text in (("profile.yaml", profile_text), ("config.yaml", settings_text.rstrip() + server_sources)):
        ok, report = check_text(text, name)
        if not ok:
            problems += [line for line in report if line.strip()]
    if not problems:
        user.profile_yaml, user.settings_yaml = profile_text, settings_text
        db.commit()
    return problems


def save_api_key(db: Session, keys: Keys, user: User, key: str) -> None:
    user.api_key_enc = keys.encrypt(key.strip())
    db.commit()


def key_hint(keys: Keys, user: User) -> str:
    key = keys.decrypt(user.api_key_enc)
    return f"saved, ends in ...{key[-4:]}" if key else "not set (CVs use templates)"


def profile_of(user: User) -> dict:
    return yaml.safe_load(user.profile_yaml or "") or {}
