"""Recognise the same job across reposts and job boards.

A company often reposts a role with a new posting id, and the same role reaches you from its careers
page and from a LinkedIn/StepStone alert. Jobs count as the same when the company matches and the
titles match after removing noise such as "(m/w/d)", "(all genders)", "Remote" or a city name.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher

APPLIED_STATUSES = ("applied", "interview", "offer", "rejected")

LEGAL_FORMS = re.compile(
    r"\b(gmbh|mbh|ag|se|kg|kgaa|ohg|ug|haftungsbeschränkt|co|inc|ltd|llc|plc|bv|nv|sa|sarl|"
    r"holding|group|deutschland|germany|europe|gruppe)\b", re.I)
GENDER_TAGS = re.compile(
    r"\((?:[^)]*\b(?:m|w|d|f|x|div|all genders?|alle geschlechter|gn|i)\b\s*/?\s*)+[^)]*\)|"
    r"\b(?:m|w|f|d)\s*/\s*(?:m|w|f|d)(?:\s*/\s*(?:m|w|f|d|x|div))?\b|\ball genders?\b|\bgn\*?", re.I)
TITLE_NOISE = re.compile(
    r"\b(remote|hybrid|on-?site|full[- ]?time|part[- ]?time|vollzeit|teilzeit|homeoffice|home office|"
    r"berlin|hamburg|munich|münchen|frankfurt|cologne|köln|germany|deutschland|bundesweit|"
    r"de|en|eu|emea|dach)\b", re.I)


def normalize_company(name: str) -> str:
    text = (name or "").lower().replace("&", " ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = LEGAL_FORMS.sub(" ", text)
    text = re.sub(r"[^a-z0-9äöüß]+", " ", text)
    return " ".join(text.split())


def _meaningful(segment: str) -> bool:
    segment = re.sub(r"\b[\w-]+\.(com|de|net|io|org)\b", " ", segment)
    segment = TITLE_NOISE.sub(" ", GENDER_TAGS.sub(" ", segment))
    return bool(re.sub(r"[^a-z0-9äöüß]+", "", segment))


def normalize_title(title: str) -> str:
    text = (title or "").lower()
    text = GENDER_TAGS.sub(" ", text)
    # Drop a trailing " - Berlin" / " | Remote" / " - Jobbird.com", but keep " - Marketing" (a different role).
    parts = re.split(r"\s[-–|@]\s", text)
    while len(parts) > 1 and not _meaningful(parts[-1]):
        parts.pop()
    text = " ".join(parts)
    text = TITLE_NOISE.sub(" ", text)
    text = re.sub(r"[^a-z0-9äöüß+#]+", " ", text)
    return " ".join(text.split())


def same_company(a: str, b: str) -> bool:
    na, nb = normalize_company(a), normalize_company(b)
    if not na or not nb:
        return False
    if na.replace(" ", "") == nb.replace(" ", ""):        # "Talon.One" vs "Talonone"
        return True
    return na == nb or na.startswith(nb + " ") or nb.startswith(na + " ")


LEVEL_WORDS = {"junior", "jr", "senior", "sr", "snr", "lead", "principal", "staff", "head", "mid", "intern",
               "werkstudent", "student", "trainee", "praktikant", "manager", "director", "i", "ii", "iii"}


def same_title(a: str, b: str) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if set(na.split()) & LEVEL_WORDS != set(nb.split()) & LEVEL_WORDS:  # Junior vs Senior = different jobs
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= 0.9


def same_job(company_a: str, title_a: str, company_b: str, title_b: str) -> bool:
    return same_company(company_a, company_b) and same_title(title_a, title_b)


def days_since(iso_day: str) -> int:
    try:
        return (date.today() - datetime.fromisoformat(iso_day[:10]).date()).days
    except (TypeError, ValueError):
        return 0  # unknown date: treat as recent
