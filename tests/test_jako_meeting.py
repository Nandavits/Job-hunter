"""The daily team meeting: members report to Jako, Jako tells the user when asked."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from jako import meeting
from jako.brain import Brain
from jako.config import JakoConfig
from jako.memory import Memory
from jako.skills import Skills
from jako.team import Team
from tests.test_jako_team import Script, say, use, FakeHands


class Hands(FakeHands):
    def online(self):
        return True

    def do(self, action, args):
        self.done.append((action, args))
        if action == "hardware_health":
            return {"text": "Battery health: 53%.\nWARNING [battery_wear]: The battery holds only 53% of its original charge."}
        if action == "top_processes":
            return {"text": "Docker 2.1 GB\nEdge 900 MB"}
        return {"text": "ok"}


@pytest.fixture
def setup(tmp_path):
    cfg = JakoConfig(api_key="t", data_dir=tmp_path, hands_token="t" * 32, jobs_database_url=f"sqlite:///{tmp_path / 'jobs.db'}")
    mem = Memory(tmp_path / "jako.db")
    skills = Skills(cfg, mem)
    hands = Hands()
    return cfg, mem, skills, hands


def brain_with(setup, *answers):
    cfg, mem, skills, hands = setup
    return Brain(cfg, mem, skills=skills, client=Script(*answers), team=Team(cfg, skills, hands=hands))


def test_phrases():
    assert meeting.intent("Jako, call a team meeting now") == "run"
    assert meeting.intent("start the stand-up") == "run"
    assert meeting.intent("what did the team report today") == "latest"
    assert meeting.intent("what's going on in my laptop") == "latest"
    assert meeting.intent("give me the daily report") == "latest"
    assert meeting.intent("meeting details please") == "details"
    assert meeting.intent("status of team report") is None          # that one is the quick roll call
    assert meeting.intent("open github") is None


def test_every_member_reports_with_real_numbers(setup):
    cfg, mem, skills, hands = setup
    reports = Team(cfg, skills, hands=hands).meeting_reports()
    assert set(reports) == {"iron_man", "hulk", "black_widow", "hawkeye", "captain_america", "thor", "vision"}
    assert "53%" in reports["hulk"] and "Docker" in reports["iron_man"]
    assert ("hardware_health", {}) in hands.done


def test_jako_holds_the_meeting_and_tells_the_summary_when_asked(setup):
    b = brain_with(setup, say("Your battery is worn to 53%, plan a new one. Everything else is fine."))
    parts = b.talk("what did the team report today", channel="voice")
    assert len(parts) == 1 and parts[0]["who"] == "jako"
    assert "battery is worn to 53%" in parts[0]["text"]
    prompt = b.client.calls[0]["messages"][0]["content"]
    assert "Hulk" in prompt and "53%" in prompt                     # Jako summarised the members' reports
    # asking again uses the saved meeting: no new AI call
    again = b.talk("what did the team say in the meeting", channel="voice")
    assert "today's team meeting" in again[0]["text"] and len(b.client.calls) == 1
    cfg = setup[0]
    assert (cfg.data_dir / "team-meetings" / f"{datetime.now(cfg.tz).date().isoformat()}.md").is_file()


def test_details_let_each_member_speak(setup):
    b = brain_with(setup, say("Summary."))
    b.talk("call a team meeting", channel="voice")
    parts = b.talk("meeting details", channel="voice")
    whos = [p["who"] for p in parts]
    assert whos[0] == "jako" and "hulk" in whos and "hawkeye" in whos and whos[-1] == "jako"


def test_meeting_runs_by_itself_every_day_but_stays_quiet(setup, monkeypatch):
    from jako import scheduler as S
    cfg = setup[0]
    b = brain_with(setup, say("Daily summary."))
    tg = SimpleNamespace(send=lambda t: (_ for _ in ()).throw(AssertionError("must not send")))
    monkeypatch.setattr(S, "hardware_alerts", lambda *a, **k: [])
    evening = datetime(2026, 11, 20, 18, 5, tzinfo=cfg.tz)
    S.tick(b, tg, now=evening)
    assert b.meetings.latest()["summary"] == "Daily summary."
    assert b.mem.take_outbox() == []                                # not announced: Jako tells when asked
    S.tick(b, tg, now=evening.replace(minute=30))                    # only once a day
    assert len(b.client.calls) == 1


def test_summary_falls_back_when_the_ai_is_down():
    class Down:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("offline")
    out = meeting.summarize(Down(), "m", "Nanda", {"hulk": "WARNING [battery_wear]: replace the battery."})
    assert "replace the battery" in out


def test_jako_can_use_the_meeting_tool_for_other_wordings(setup):
    b = brain_with(setup, use("team_meeting", action="latest"), say("Meeting summary."),
                   say("Today the team found that your battery is worn."))
    parts = b.talk("anything I should know about today?", channel="voice")
    assert parts[-1]["text"] == "Today the team found that your battery is worn."
