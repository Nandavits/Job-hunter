"""When did the job search (in Docker) last run, and what did it find? Shown at the top of the dashboard.

The scheduler and the run write data/last-run.json; the dashboard (on Windows) reads it.
"""
from __future__ import annotations

import datetime as dt
import json

from .config import DATA_DIR

FILE = None                                  # default: data/last-run.json


def _path():
    return FILE or DATA_DIR / "last-run.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update(**values) -> None:
    data = load()
    if values.get("running") and "finished" in data:     # a new run starts: keep only when the last one ended
        data = {"last_finished": data["finished"], "last_new": data.get("new"),
                "every_minutes": data.get("every_minutes")}
    data.update(values)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _time(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def label(now: dt.datetime | None = None) -> dict:
    """{'text': 'Last search 18:06 · 4 new · next about 18:36', 'warn': False} for the dashboard."""
    data, now = load(), now or dt.datetime.now()
    every = int(data.get("every_minutes") or 30)
    if not data:
        return {"text": "No job search has finished yet", "warn": False}
    if data.get("running"):
        started = _time(data.get("started"))
        if started and (now - started).total_seconds() > 3 * 3600:
            return {"text": f"Job search started {started:%d %b %H:%M} and did not finish - is Docker Desktop running?",
                    "warn": True}
        last = _time(data.get("last_finished"))
        before = f" · last finished {last:%H:%M}" if last else ""
        return {"text": f"Searching for jobs now (started {started:%H:%M}){before}" if started else
                "Searching for jobs now", "warn": False}
    finished = _time(data.get("finished"))
    if not finished:
        return {"text": "No job search has finished yet", "warn": False}
    day = "" if finished.date() == now.date() else f"{finished:%d %b} "
    parts = [f"Last search {day}{finished:%H:%M}"]
    if data.get("new") is not None:
        parts.append(f"{data['new']} new job{'s' if data['new'] != 1 else ''}")
    if data.get("ok") is False:
        parts.append("it had an error (see logs)")
    late = (now - finished).total_seconds() > (2 * every + 15) * 60
    if late:
        parts.append("no search since then - is Docker Desktop running?")
    else:
        parts.append(f"next about {finished + dt.timedelta(minutes=every):%H:%M}")
    return {"text": " · ".join(parts), "warn": late or data.get("ok") is False}
