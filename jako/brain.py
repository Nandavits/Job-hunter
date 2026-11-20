"""Jako's brain. Jako is the boss: it answers simple things itself and gives tasks to its team
(Iron Man, Black Widow, Hawkeye, Captain America, Thor). Each member reports back in its own voice.

Safety: laptop actions that change something wait for the user's "yes". That check happens here in code,
from the user's own message; no AI, email, web page or file can approve it.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from .config import JakoConfig
from .memory import Memory
from .skills import TOOLS, Skills
from .team import AGENTS, DELEGATE_TOOL, Team
from . import meeting

log = logging.getLogger("jako")

PERSONA = """You are Jako, {name}'s personal assistant, like Alexa but smarter, and the leader of a team.
{name} lives in {city}, is finishing an M.Sc. in Data Science and is looking for a data engineering / analytics
job in Germany.

Right now it is {now} ({tz}).

Your team (use the ask_team tool):
- iron_man: WhatsApp calls and messages (right away, no yes needed), and the laptop (open apps incl. WhatsApp, websites like GitHub/LinkedIn, files and folders, find and read
  files, volume, and - only with the user's yes - commands, writing, moving, deleting files, closing apps)
- black_widow: email and calendar (read-only)
- hawkeye: the Job Hunter (top jobs, details, marking a job applied/interview/rejected/...)
- captain_america: reminders, to-dos, planning the day
- thor: web search, news, facts, prices, rules, anything current, and the weather
- hulk: laptop health (performance today and day to day, busiest programs, disk space, old unused files, cleaning up)
- vision: looks at the screen and reviews the user's work (code, pipelines, SQL, documents, errors) when asked
You are the boss: every day the team holds a team meeting and reports to you (team_meeting tool). When {name}
asks what's going on, what the team reported, or for the daily report, use it and tell them what matters.

How to work:
- Small talk, opinions, explanations and things you know well: answer yourself.
- Anything about the laptop, email, calendar, jobs, reminders, to-dos, the weather or current facts: give it to
  the right member with a complete task. Use several members at once when the request has several parts.
- Members' reports are spoken to {name} in their own voices before your answer. Do not repeat or summarise them.
  After reports, add one short sentence only if it adds something new (a warning, a connection, a question);
  otherwise answer with exactly "-" and nothing else.
- Never claim an action was done unless a member reported it. You cannot send email, apply to jobs or approve
  laptop actions; laptop changes wait for {name}'s yes.
- {style}
- Reply in the language of {name}'s latest message (English question -> English answer).
- Interview practice: ask one question at a time and give short, honest feedback.

What you know about {name}:
{facts}"""

STYLES = {
    "voice": "Your answer is spoken aloud: 1-3 short sentences, no lists, no markdown, no links or ids unless asked.",
    "telegram": "Your answer is read on a phone: short paragraphs or a short list, plain text, no markdown tables.",
}

MEETING_TOOL = {"name": "team_meeting", "description": (
    "The daily team meeting, where every member reports to you on the laptop, health, email, jobs, plans and weather. "
    "action 'latest' gives the last meeting's summary and reports, 'run' holds a new meeting now (takes up to a "
    "minute). Use it when the user asks what the team reported, what's going on today, or for the daily report."),
    "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["latest", "run"]}}}}
OWN_TOOLS = [t for t in TOOLS if t["name"] in ("remember", "forget")] + [DELEGATE_TOOL, MEETING_TOOL]

ROLL_CALL = re.compile(r"^\W*(?:(?:hello|hi|hey|hallo|ok(?:ay)?)\W+)?(?:jako\W+)?(?:wake up|roll call|team status|"
                       r"status report|avengers,? assemble|everyone report|team,? report|who is (?:awake|there))\W*$",
                       re.I)
TEAM_STATUS = re.compile(r"\b(?:team|avengers|everyone|everybody)\b.*\b(?:status|report|update)\b|"
                         r"\b(?:status|report|update)\b.*\b(?:team|avengers|everyone|everybody)\b", re.I)


def is_roll_call(text: str) -> bool:
    """Short requests about the team's status get the instant report (no AI, answers in seconds)."""
    return bool(ROLL_CALL.match(text) or (TEAM_STATUS.search(text) and len(text.split()) <= 9))


YES = re.compile(r"^\W*(?:(?:yes|yeah|yep|yup|sure|ja|jawohl|ok(?:ay)?|confirm(?:ed)?|of course)\b(?!.*\b(?:no|not|don'?t|nicht|nein)\b)"
                 r"|(?:do it|go ahead|mach (?:das|es)|go for it)\b)", re.I)
NO = re.compile(r"^\W*(no|nope|nein|cancel|stop|don'?t|do not|never ?mind)\b.*$", re.I)


def as_text(parts: list[dict]) -> str:
    """One message for Telegram: each team member on its own line with its emoji and name."""
    if len(parts) == 1 and parts[0]["who"] == "jako":
        return parts[0]["text"]
    return "\n\n".join(f"{AGENTS[p['who']]['emoji']} {AGENTS[p['who']]['name']}: {p['text']}" for p in parts)


class Brain:
    def __init__(self, cfg: JakoConfig, memory: Memory, skills: Skills | None = None, client=None, team=None):
        self.cfg, self.mem = cfg, memory
        self.skills = skills or Skills(cfg, memory)
        self.team = team or Team(cfg, self.skills)
        self.client = client
        self.pending: dict[str, dict] = {}            # channel -> {"code", "summary", "at"}
        self.meetings = meeting.Meetings(memory)

    def _client(self):
        if self.client is None:
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.cfg.api_key)
        return self.client

    def system(self, channel: str) -> str:
        now = datetime.now(self.cfg.tz)
        facts = "\n".join(f"- {f}" for f in self.mem.facts()) or "- (nothing saved yet)"
        return PERSONA.format(name=self.cfg.owner_name, city=self.cfg.city, tz=self.cfg.timezone,
                              now=now.strftime("%A, %d %B %Y, %H:%M"), facts=facts,
                              style=STYLES.get(channel, STYLES["telegram"]))

    # ------------------------------------------------------------------ main entry
    def talk(self, text: str, channel: str = "telegram", remember: bool = True) -> list[dict]:
        """Returns what to say, as parts: [{"who": "iron_man", "text": "..."}, {"who": "jako", "text": "..."}]."""
        text = text.strip()
        gated = self._confirmation(text, channel)
        if gated is not None:
            return gated
        kind = meeting.intent(text)
        if kind:                                      # the daily team meeting: Jako tells what the team reported
            parts = meeting.answer(self, kind, "German" if re.search(r"\b(ich|bitte|mein|was|wie)\b", text, re.I) else "English")
            if remember:
                self.mem.add_message(channel, "user", text)
                self.mem.add_message(channel, "assistant", as_text(parts))
            return parts
        if is_roll_call(text):
            return self.team.roll_call()
        if not self.cfg.api_key and self.client is None:
            return [{"who": "jako", "text": "I need a Claude API key first. Add ANTHROPIC_API_KEY to the .env file."}]

        lang = "German" if re.search(r"\b(ich|du|bitte|und|nicht|ist|wie|was|mein|mir)\b", text, re.I) else "English"
        messages = (self.mem.history(channel) if remember else []) + [{"role": "user", "content": text}]
        reports: list[dict] = []
        new_pending: list[dict] = []
        reply = ""
        system = self.system(channel)                 # fixed for the whole question: the AI rejects a follow-up call
        for _ in range(6):                            # whose instructions changed (e.g. the clock moved a minute)
            msg = self._client().messages.create(model=self.cfg.model, max_tokens=1500, system=system,
                                                 tools=OWN_TOOLS, messages=messages)
            reply = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip()
            calls = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
            if not calls:
                break
            team_calls = [c for c in calls if c.name == "ask_team" and dict(c.input).get("member") in AGENTS
                          and dict(c.input).get("member") != "jako"]
            jobs = [(dict(c.input)["member"], dict(c.input).get("task", "")) for c in team_calls]
            done = dict(zip([c.id for c in team_calls],
                            self.team.run_many(self._client(), jobs, lang, new_pending, channel) if jobs else []))
            results = []
            for call in calls:
                args = dict(call.input)
                if call.id in done:
                    member, report = args["member"], done[call.id]
                    log.info("%s reports: %s", AGENTS[member]["name"], report[:150])
                    reports.append({"who": member, "text": report})
                    out = f"{AGENTS[member]['name']} reported (already spoken to the user): {report}"
                elif call.name == "team_meeting":
                    m = meeting.hold(self, lang) if args.get("action") == "run" or not self.meetings.latest() \
                        else self.meetings.latest()
                    out = (f"Team meeting of {m['day']}. Your summary then: {m['summary']}\nReports:\n" +
                           "\n".join(f"- {AGENTS[a]['name']}: {r}" for a, r in m["reports"].items()))
                else:
                    out = self.skills.run(call.name, args)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": out[:8000]})
            messages = messages + [{"role": "assistant", "content": msg.content}, {"role": "user", "content": results}]

        if new_pending:                                   # only the latest request can be approved
            self.pending[channel] = {**new_pending[-1], "at": time.time()}
        if reports and (not reply.strip(" -.") or re.search(r"nothing (?:more )?to add|has covered it", reply, re.I)):
            reply = ""
        parts = reports + ([{"who": "jako", "text": reply}] if reply else [])
        if not parts:
            parts = [{"who": "jako", "text": "Sorry, I couldn't finish that. Please try again."}]
        if remember:
            self.mem.add_message(channel, "user", text)
            self.mem.add_message(channel, "assistant", as_text(parts))
        return parts

    def ask(self, text: str, channel: str = "telegram", remember: bool = True) -> str:
        return as_text(self.talk(text, channel, remember))

    # ------------------------------------------------------------------ the yes/no gate (code, not AI)
    def _confirmation(self, text: str, channel: str) -> list[dict] | None:
        item = self.pending.get(channel)
        if not item:
            return None
        if time.time() - item["at"] > 300:
            self.pending.pop(channel, None)
            self.team.hands.cancel(item["code"])
            return None
        if YES.match(text) and len(text.split()) <= 8:             # "yes", "yes close it", "ok do it"
            self.pending.pop(channel, None)
            out = self.team.hands.confirm(item["code"])
            said = out.get("error") or out.get("text") or "Done."
            self.mem.add_message(channel, "user", text)
            self.mem.add_message(channel, "assistant", f"Iron Man: {said}")
            return [{"who": "iron_man", "text": said}]
        self.pending.pop(channel, None)                   # anything else cancels the waiting action
        self.team.hands.cancel(item["code"])
        if NO.match(text):
            return [{"who": "iron_man", "text": "Okay, I won't do it."}]
        return None

    def briefing(self) -> str:
        return self.ask(
            "Give me my morning briefing: today's weather, my calendar today, open reminders and to-dos, "
            "new important emails (interviews, recruiters, rejections) and the job hunter status with my top 3 jobs. "
            "Keep it short and end with one suggestion for today.", channel="telegram", remember=False)
