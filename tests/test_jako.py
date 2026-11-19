from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from jako.brain import Brain
from jako.config import JakoConfig
from jako.memory import Memory
from jako.scheduler import tick
from jako.skills import Skills
from jako.telegram_bot import Telegram


@pytest.fixture
def cfg(tmp_path):
    return JakoConfig(api_key="test", data_dir=tmp_path, telegram_token="T", telegram_chat_id="42",
                      jobs_database_url=f"sqlite:///{tmp_path / 'jobs.db'}")


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "jako.db")


class Resp:
    def __init__(self, data=None, content=b""):
        self._data, self.content = data, content

    def json(self):
        return self._data


class FakeHTTP:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        return next(r for k, r in self.routes.items() if k in url)

    def post(self, url, data=None, **kw):
        self.calls.append((url, data))
        return Resp({"ok": True, "result": {}})


def test_weather_text(cfg, mem):
    http = FakeHTTP({"api.open-meteo.com": Resp({
        "current": {"temperature_2m": 4.2, "apparent_temperature": 1.0, "weather_code": 61, "wind_speed_10m": 18},
        "daily": {"time": ["2026-11-19"], "weather_code": [3], "temperature_2m_min": [1],
                  "temperature_2m_max": [6], "precipitation_probability_max": [70]}})})
    text = Skills(cfg, mem, http).get_weather()
    assert "Berlin now: 4.2°C" in text and "light rain" in text and "rain chance 70%" in text


def test_reminders_and_todos(cfg, mem):
    s = Skills(cfg, mem)
    when = (datetime.now(cfg.tz) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    assert "Reminder 1 set" in s.add_reminder("call the Ausländerbehörde", when)
    assert "past" in s.add_reminder("too late", "2020-01-01T10:00")
    assert "Ausländerbehörde" in s.list_reminders()
    assert "cancelled" in s.cancel_reminder(1) and s.list_reminders() == "No open reminders."
    s.add_todo("buy milk")
    assert "1: buy milk" in s.list_todos() and "done" in s.complete_todo(1)
    s.remember("Prefers answers in English")
    assert mem.facts() == ["Prefers answers in English"]


def test_job_hunter_skills(cfg, mem):
    from jobhunter.models import Job
    from jobhunter.storage import Store
    store = Store(cfg.jobs_database_url)
    job = Job("ashby", "1", "Flink", "Data Engineer", "https://flink", description="x" * 200)
    job.score = 95
    store.save([job])
    s = Skills(cfg, mem)
    assert "Flink – Data Engineer" in s.jobs_overview()
    assert "now 'applied'" in s.mark_job("flink", "applied")
    assert store.get(job.uid)["status"] == "applied"


def test_calendar_from_ical(cfg, mem):
    now = datetime.now(cfg.tz)
    start = now.replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ics = (f"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:1\nDTSTART;TZID=Europe/Berlin:{start:%Y%m%dT%H%M%S}\n"
           f"DTEND;TZID=Europe/Berlin:{start:%Y%m%dT%H%M%S}\nSUMMARY:Interview Flink\nLOCATION:Zoom\n"
           f"END:VEVENT\nEND:VCALENDAR\n").encode()
    cfg.calendar_ics_url = "https://calendar.example/secret.ics"
    text = Skills(cfg, mem, FakeHTTP({"calendar.example": Resp(content=ics)})).calendar(days=3)
    assert "Interview Flink @ Zoom" in text and "15:00" in text


class Block(SimpleNamespace):
    pass


class FakeClaude:
    """First answer asks for the weather tool, second answer uses the result."""
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                Block(type="tool_use", id="t1", name="list_todos", input={})])
        result = kw["messages"][-1]["content"][0]["content"]
        return SimpleNamespace(stop_reason="end_turn", content=[Block(type="text", text=f"Your list: {result}")])


def test_brain_uses_tools_and_remembers(cfg, mem):
    mem.add_todo("send CV to Flink")
    brain = Brain(cfg, mem, client=FakeClaude())
    reply = brain.ask("what is on my to-do list?", channel="voice")
    assert "send CV to Flink" in reply
    assert [m["role"] for m in mem.history("voice")] == ["user", "assistant"]
    assert "spoken aloud" in brain.client.calls[0]["system"]
    assert any(t.get("name") == "web_search" for t in brain.client.calls[0]["tools"])


def test_telegram_only_answers_the_owner(cfg, mem):
    http = FakeHTTP({})
    brain = SimpleNamespace(ask=lambda text, channel: f"echo {text}", mem=mem, briefing=lambda: "b")
    tg = Telegram(cfg, brain, http)
    tg.handle({"chat": {"id": 999}, "text": "hello"})
    assert not any("sendMessage" in c[0] for c in http.calls if isinstance(c, tuple))
    tg.handle({"chat": {"id": 42}, "text": "hello"})
    sent = [c[1] for c in http.calls if isinstance(c, tuple) and "sendMessage" in c[0]]
    assert sent and sent[-1]["text"] == "echo hello"


def test_telegram_setup_shows_chat_id(cfg, mem):
    cfg.telegram_chat_id = ""
    http = FakeHTTP({})
    Telegram(cfg, SimpleNamespace(mem=mem), http).handle({"chat": {"id": 777}, "text": "hi"})
    assert "TELEGRAM_CHAT_ID=777" in [c[1] for c in http.calls if isinstance(c, tuple)][-1]["text"]


def test_due_reminder_is_announced_once(cfg, mem):
    mem.add_reminder("drink water", datetime.now(cfg.tz) - timedelta(minutes=1))
    sent = []
    tg = SimpleNamespace(send=lambda t: sent.append(t))
    brain = SimpleNamespace(cfg=cfg, mem=mem, briefing=lambda: "brief")
    noon = datetime.now(cfg.tz).replace(hour=13, minute=0)
    tick(brain, tg, now=noon)
    tick(brain, tg, now=noon)
    assert sent == ["⏰ Reminder: drink water"]
    assert mem.take_outbox() == ["Reminder: drink water"]


def test_briefing_once_a_morning(cfg, mem):
    sent = []
    tg = SimpleNamespace(send=lambda t: sent.append(t))
    brain = SimpleNamespace(cfg=cfg, mem=mem, briefing=lambda: "Sunny, 2 interviews")
    morning = datetime.now(cfg.tz).replace(hour=8, minute=5)
    tick(brain, tg, now=morning)
    tick(brain, tg, now=morning + timedelta(minutes=30))
    assert len(sent) == 1 and "Sunny" in sent[0]


def test_web_page_and_api(cfg, mem):
    from fastapi.testclient import TestClient
    from jako.server import create_app
    brain = SimpleNamespace(ask=lambda text, channel: f"heard {text} via {channel}", mem=mem)
    client = TestClient(create_app(brain))
    assert "Hey Jako" in client.get("/").text
    assert client.post("/api/ask", json={"text": "hi"}).json() == {"reply": "heard hi via voice"}
    mem.say_later("Reminder: stretch")
    assert client.get("/api/outbox").json() == {"items": ["Reminder: stretch"]}
