"""The daily team meeting: every member reports to Jako, Jako writes the boss summary.

Runs by itself every day (JAKO_MEETING_TIME, default 18:00) and whenever the user asks for one. Jako does not
announce it: the user asks ("Jako, what did the team report today?") and Jako tells them. The full minutes are
kept in Jako's memory and as a small text file per day in data/team-meetings/.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime

log = logging.getLogger("jako")

RUN = re.compile(r"\b(?:call|start|hold|have|run|do|begin|schedule)\b.{0,25}\b(?:team )?(?:meeting|stand-?up|team call)\b", re.I)
DETAILS = re.compile(r"\b(?:meeting|stand-?up|team call)\b.{0,40}\b(?:details?|full|everyone|each|every member|minutes)\b|"
                     r"\b(?:details?|full|minutes)\b.{0,30}\b(?:meeting|stand-?up|team call)\b", re.I)
ASK = re.compile(r"\b(?:meeting|stand-?up|team call|daily report)\b|"
                 r"\bwhat did (?:the )?(?:team|everyone|everybody|avengers) (?:say|report|find)\b|"
                 r"\bwhat'?s (?:been )?going on (?:in|on|with) my (?:laptop|computer|pc)\b", re.I)

SCHEMA = """
create table if not exists meetings (
    id integer primary key autoincrement,
    day text not null,
    at text not null,
    reports text not null,
    summary text not null
);
"""

SUMMARY_PROMPT = """You are Jako, the boss of {owner}'s assistant team. Your team just held the daily team meeting and
each member reported to you (below). Tell {owner} what matters, in {lang}, as you would speak it: 3 to 5 short
sentences, no lists, no markdown. Start with anything that needs action or attention (warnings, problems, things due),
then the good news. Use numbers from the reports. Don't invent anything that isn't in the reports.

Reports:
{reports}"""

_running = threading.Lock()


def intent(text: str) -> str | None:
    if RUN.search(text):
        return "run"
    if DETAILS.search(text):
        return "details"
    if ASK.search(text):
        return "latest"
    return None


class Meetings:
    def __init__(self, mem):
        self.mem = mem
        with mem.lock:
            mem.db.executescript(SCHEMA)

    def save(self, day: str, reports: dict, summary: str) -> None:
        self.mem._q("insert into meetings (day, at, reports, summary) values (?, ?, ?, ?)",
                    (day, datetime.now().isoformat(timespec="minutes"), json.dumps(reports), summary))

    def latest(self) -> dict | None:
        rows = self.mem._q("select * from meetings order by id desc limit 1")
        if not rows:
            return None
        r = rows[0]
        return {"day": r["day"], "at": r["at"], "reports": json.loads(r["reports"]), "summary": r["summary"]}


def summarize(client, model: str, owner: str, reports: dict, lang: str = "English") -> str:
    from .team import AGENTS
    text = "\n".join(f"- {AGENTS[a]['name']}: {r}" for a, r in reports.items())
    try:
        msg = client.messages.create(model=model, max_tokens=500, messages=[{"role": "user", "content":
                                     SUMMARY_PROMPT.format(owner=owner, lang=lang, reports=text)}])
        out = "".join(getattr(b, "text", "") for b in msg.content).strip()
        if out:
            return out
    except Exception as exc:
        log.warning("Meeting summary failed: %s", exc)
    warn = [r for r in reports.values() if "WARNING" in r or "⚠" in r]
    return ("The team has reported. " + (" ".join(warn) if warn else "Nothing urgent came up.")).strip()


def hold(brain, lang: str = "English") -> dict:
    """Run the meeting now: every member reports, Jako summarises, the minutes are saved."""
    with _running:
        cfg = brain.cfg
        reports = brain.team.meeting_reports()
        summary = summarize(brain._client(), cfg.team_model, cfg.owner_name, reports, lang)
        day = datetime.now(cfg.tz).date().isoformat()
        brain.meetings.save(day, reports, summary)
        _write_minutes(cfg.data_dir, day, reports, summary)
        log.info("Team meeting held: %s", summary[:200])
        return {"day": day, "reports": reports, "summary": summary}


def _write_minutes(data_dir, day: str, reports: dict, summary: str) -> None:
    from .team import AGENTS
    folder = data_dir / "team-meetings"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"# Team meeting {day} ({datetime.now():%H:%M})", "", "## Jako's summary", summary, "", "## Reports"]
    lines += [f"- {AGENTS[a]['emoji']} **{AGENTS[a]['name']}**: {r}" for a, r in reports.items()]
    (folder / f"{day}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def answer(brain, kind: str, lang: str = "English") -> list[dict]:
    """What Jako says when the user asks about the meeting."""
    cfg = brain.cfg
    today = datetime.now(cfg.tz).date().isoformat()
    m = brain.meetings.latest()
    if kind == "run" or m is None:
        m = hold(brain, lang)
        intro = "The team just met. Here's what they told me. "
    elif m["day"] == today:
        intro = f"In today's team meeting at {m['at'][11:16]}, here's what the team told me. "
    else:
        intro = f"The last team meeting was on {m['day']}. Here's what the team told me then. "
    if kind == "details":
        from .team import AGENTS
        parts = [{"who": "jako", "text": f"Here are the full reports from the team meeting on {m['day']}."}]
        parts += [{"who": a, "text": r if r.startswith(AGENTS[a]["name"]) else f"{AGENTS[a]['name']} here. {r}"}
                  for a, r in m["reports"].items()]
        parts.append({"who": "jako", "text": m["summary"]})
        return parts
    return [{"who": "jako", "text": intro + m["summary"] + " Say “meeting details” to hear each member's full report."}]
