"""Jako's brain: Claude with Jako's skills and web search, plus short-term memory per channel."""
from __future__ import annotations

import logging
from datetime import datetime

from .config import JakoConfig
from .memory import Memory
from .skills import TOOLS, Skills

log = logging.getLogger("jako")

PERSONA = """You are Jako, {name}'s personal assistant, like Alexa but smarter. {name} lives in {city}, is finishing an
M.Sc. in Data Science and is looking for a data engineering / analytics job in Germany. A tool called Job Hunter
finds and scores jobs for {name}; you can read it and update statuses.

Right now it is {now} ({tz}).

How to answer:
- {style}
- Answer in the language {name} uses. Be warm, direct and practical. No filler.
- Use your tools for facts about {name}'s day, jobs, email, calendar, reminders and weather. Use web search for
  current events, prices, opening hours, rules and anything that may have changed. Never invent facts.
- For reminders, turn relative times into an exact local time yourself, then confirm the time you set.
- You can read email and the calendar, but you cannot send email, delete anything or apply to jobs. Say so if asked.
- Interview practice: ask one question at a time and give short, honest feedback.

What you know about {name}:
{facts}"""

STYLES = {
    "voice": "Your answer is spoken aloud: 1-3 short sentences, no lists, no markdown, no links or ids unless asked.",
    "telegram": "Your answer is read on a phone: short paragraphs or a short list, plain text, no markdown tables.",
}

WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}


class Brain:
    def __init__(self, cfg: JakoConfig, memory: Memory, skills: Skills | None = None, client=None):
        self.cfg, self.mem = cfg, memory
        self.skills = skills or Skills(cfg, memory)
        self.client = client

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

    def ask(self, text: str, channel: str = "telegram", remember: bool = True) -> str:
        if not self.cfg.api_key and self.client is None:
            return "I need a Claude API key first. Add ANTHROPIC_API_KEY to the .env file."
        messages = (self.mem.history(channel) if remember else []) + [{"role": "user", "content": text}]
        reply = ""
        for _ in range(8):                                  # tool rounds
            msg = self._client().messages.create(
                model=self.cfg.model, max_tokens=2500, system=self.system(channel),
                tools=TOOLS + [WEB_SEARCH], messages=messages)
            reply = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip()
            if msg.stop_reason == "pause_turn":            # long web search: let it continue
                messages = messages + [{"role": "assistant", "content": msg.content}]
                continue
            calls = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
            if not calls:
                break
            results = []
            for call in calls:
                output = self.skills.run(call.name, dict(call.input))
                log.info("tool %s(%s) -> %s", call.name, dict(call.input), output[:120].replace("\n", " "))
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": output[:8000]})
            messages = messages + [{"role": "assistant", "content": msg.content},
                                   {"role": "user", "content": results}]
        reply = reply or "Sorry, I couldn't finish that. Please try again."
        if remember:
            self.mem.add_message(channel, "user", text)
            self.mem.add_message(channel, "assistant", reply)
        return reply

    def briefing(self) -> str:
        return self.ask(
            "Give me my morning briefing: today's weather, my calendar today, open reminders and to-dos, "
            "new important emails (interviews, recruiters, rejections) and the job hunter status with my top 3 jobs. "
            "Keep it short and end with one suggestion for today.", channel="telegram", remember=False)
