"""The normalised job record every collector produces."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import unescape


def html_to_text(html: str | None) -> str:
    """Strip HTML tags and collapse whitespace (good enough for keyword matching)."""
    if not html:
        return ""
    text = unescape(html)  # Greenhouse double-encodes HTML
    text = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


@dataclass
class Job:
    source: str                 # greenhouse | lever | ashby | personio | arbeitsagentur | email
    external_id: str            # id inside the source
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""       # plain text
    apply_url: str = ""
    posted_at: str = ""         # ISO date if known
    remote: bool = False
    extra: dict = field(default_factory=dict)

    # filled in by the filter step
    score: int = 0
    german_required: bool = False
    excluded_reason: str = ""
    matched_skills: list[str] = field(default_factory=list)

    @property
    def uid(self) -> str:
        key = f"{self.source}:{self.company}:{self.external_id}".lower()
        return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16]   # an ID, not security

    @property
    def application_url(self) -> str:
        return self.apply_url or self.url

    def to_row(self) -> dict:
        row = asdict(self)
        row.pop("extra")
        row["uid"] = self.uid
        row["matched_skills"] = ",".join(self.matched_skills)
        row["collected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return row
