"""Companies Job Hunter finds by itself.

Every job ad that links to a company's own careers platform (Personio, Greenhouse, Lever, Ashby,
SmartRecruiters, Recruitee) adds that company here for good, so its careers page is checked on every
run, also for jobs that never reach the job agency or a job board. Small start-ups and medium-sized
companies mostly use these platforms. The list grows with every search; nothing to maintain.

Stored in data/discovered-companies.json. Companies you list yourself in config.yaml come first.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .collectors.ats import FETCHERS, detect_ats
from .config import DATA_DIR

log = logging.getLogger("jobhunter")
FILE = None             # default: data/discovered-companies.json (tests point it elsewhere)


def _path(path) -> Path:
    return Path(path) if path else (FILE or DATA_DIR / "discovered-companies.json")
PER_RUN = 150            # careers pages checked per run; the ones checked longest ago go first


def _key(entry: dict) -> str:
    return f"{entry['ats']}:{entry['slug'].lower()}"


def load(path=None) -> dict:
    try:
        return json.loads(_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict, path=None) -> None:
    try:
        path = _path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save the discovered companies: %s", exc)


def learn(items, configured: list[dict] | None = None, path=None) -> int:
    """Add the careers platforms mentioned in jobs (objects or dict rows). Returns how many are new."""
    data = load(path)
    mine = {_key(c) for c in configured or [] if c.get("ats") and c.get("slug")}
    today = dt.date.today().isoformat()
    added = 0
    for item in items:
        get = item.get if isinstance(item, dict) else (lambda k, _i=item: getattr(_i, k, ""))
        text = " ".join(str(get(k) or "") for k in ("url", "apply_url", "description"))
        for entry in detect_ats(text):
            if entry["ats"] not in FETCHERS:
                continue
            key = _key(entry)
            if key in data or key in mine:
                continue
            data[key] = {**entry, "name": str(get("company") or entry["slug"]).strip()[:80],
                         "found": today, "checked": "", "jobs": None}
            added += 1
    if added:
        save(data, path)
        log.info("Found %d new company careers pages (now %d)", added, len(data))
    return added


def due(limit: int = PER_RUN, path=None) -> list[dict]:
    """The companies to check this run: never checked first, then the longest ago."""
    data = load(path)
    items = sorted(data.values(), key=lambda c: c.get("checked") or "")
    return [c for c in items if not c.get("dead")][:limit]


def mark(company: dict, jobs: int | None, path=None) -> None:
    data = load(path)
    key = _key(company)
    if key in data:
        entry = data[key]
        entry["checked"] = dt.datetime.now().isoformat(timespec="seconds")
        entry["fails"] = 0 if jobs is not None else entry.get("fails", 0) + 1
        entry["jobs"] = jobs
        if entry["fails"] >= 5:                      # page gone for good
            entry["dead"] = True
        save(data, path)
