"""Posting dates: read them in any common format, find them in ad text, and say how old a job is."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS.update({"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7,
               "oktober": 10, "dezember": 12, "okt": 10, "dez": 12, "mär": 3})

_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)")
DMY_DOTS = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d\d)\b")
DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH})\.?,?\s+(20\d\d)\b", re.I)
MONTH_DAY_YEAR = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b", re.I)


def _make(y, m, d) -> str:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return ""


def normalize_date(value) -> str:
    """'2026-11-23T10:00Z', 'October  2, 2026', '27 October 2026', '23.11.2026', 1795000000000 -> 'YYYY-MM-DD'."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        if n > 1e11:
            n /= 1000                                  # milliseconds
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip()
    if m := ISO.search(text):
        return _make(*m.groups())
    if m := DMY_DOTS.search(text):
        return _make(m.group(3), m.group(2), m.group(1))
    if m := DAY_MONTH_YEAR.search(text):
        return _make(m.group(3), MONTHS[m.group(2).lower()], m.group(1))
    if m := MONTH_DAY_YEAR.search(text):
        return _make(m.group(3), MONTHS[m.group(1).lower()], m.group(2))
    try:
        return parsedate_to_datetime(text).date().isoformat()       # email Date header
    except (TypeError, ValueError, IndexError):
        return ""


def date_in_ad(text: str, today: date | None = None) -> str:
    """A posting date near the top of an ad ('Berlin, Germany · 27 October 2026'), if it is not in the future."""
    head = (text or "")[:400]
    for pattern in (DAY_MONTH_YEAR, MONTH_DAY_YEAR, ISO, DMY_DOTS):
        m = pattern.search(head)
        if m:
            found = normalize_date(m.group(0))
            if found and found <= (today or date.today()).isoformat():
                return found
    return ""


def age_days(posted: str, today: date | None = None) -> int | None:
    iso = normalize_date(posted)
    if not iso:
        return None
    return max(0, ((today or date.today()) - date.fromisoformat(iso)).days)


def age_text(posted: str, today: date | None = None) -> str:
    days = age_days(posted, today)
    if days is None:
        return ""
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
