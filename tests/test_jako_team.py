"""Jako's team: delegation, roll call, the yes/no gate for laptop changes, and the laptop safety rules."""
from types import SimpleNamespace

import pytest

from jako import hands as H
from jako.brain import Brain, as_text
from jako.config import JakoConfig
from jako.memory import Memory
from jako.skills import Skills
from jako.team import Team


@pytest.fixture
def cfg(tmp_path):
    return JakoConfig(api_key="test", data_dir=tmp_path, hands_token="t" * 32,
                      jobs_database_url=f"sqlite:///{tmp_path / 'jobs.db'}")


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "jako.db")


def block(**kw):
    return SimpleNamespace(**kw)


class Script:
    """Fake Claude that plays back answers in order, for Jako and the team together."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.answers.pop(0)


def say(text):
    return SimpleNamespace(stop_reason="end_turn", content=[block(type="text", text=text)])


def use(tool, **args):
    return SimpleNamespace(stop_reason="tool_use", content=[block(type="tool_use", id=f"id_{tool}", name=tool,
                                                                  input=args)])


class FakeHands:
    def __init__(self):
        self.done, self.confirmed, self.cancelled = [], [], []

    def do(self, action, args):
        self.done.append((action, args))
        if action == "delete":
            return {"pending": "abc123", "text": f"Needs your OK: move {args['path']} to the Recycle Bin"}
        return {"text": f"{action} ok"}

    def confirm(self, code):
        self.confirmed.append(code)
        return {"text": "Moved old.txt to the Recycle Bin."}

    def cancel(self, code):
        self.cancelled.append(code)
        return {"text": "Cancelled."}

    def status(self):
        return {"ok": True, "computer": "LAPTOP-FDG", "pending": 0}


def make(cfg, mem, client, hands=None):
    skills = Skills(cfg, mem)
    return Brain(cfg, mem, skills=skills, client=client, team=Team(cfg, skills, hands=hands or FakeHands()))


def test_jako_gives_task_to_iron_man_who_reports_in_own_voice(cfg, mem):
    client = Script(use("ask_team", member="iron_man", task="Open Notepad"),   # Jako delegates
                    use("open_app", name="notepad"),                            # Iron Man uses his tool
                    say("Notepad is open."),                                    # Iron Man reports
                    say(""))                                                    # Jako adds nothing
    hands = FakeHands()
    parts = make(cfg, mem, client, hands).talk("open notepad", channel="voice")
    assert parts == [{"who": "iron_man", "text": "Notepad is open."}]
    assert hands.done == [("open_app", {"name": "notepad"})]
    jako_tools = [t["name"] for t in client.calls[0]["tools"]]
    assert {"remember", "ask_team", "team_meeting", "run_command", "whatsapp_call", "read_email", "web_search"} <= set(jako_tools)
    assert "Iron Man" in client.calls[1]["system"] and client.calls[1]["tools"][0]["name"] == "open_app"


def test_members_only_get_their_own_tools(cfg, mem):
    client = Script(use("ask_team", member="hawkeye", task="mark Flink applied"),
                    use("read_email"),                          # not Hawkeye's tool
                    say("I can't read email, that's Black Widow."), say("Ask me again for email."))
    parts = make(cfg, mem, client).talk("mark flink applied")
    tool_result = client.calls[2]["messages"][-1]["content"][0]
    assert tool_result["is_error"] and tool_result["content"] == "Not your tool."
    assert parts[0]["who"] == "hawkeye" and parts[-1]["who"] == "jako"


def test_delete_waits_for_yes_and_only_the_user_can_confirm(cfg, mem):
    hands = FakeHands()
    client = Script(use("ask_team", member="iron_man", task="delete old.txt"),
                    use("delete", path="old.txt"),
                    say("Should I move old.txt to the Recycle Bin? Say yes or no."), say(""))
    brain = make(cfg, mem, client, hands)
    brain.talk("delete old.txt", channel="voice")
    assert hands.confirmed == [] and brain.pending["voice"]["code"] == "abc123"
    parts = brain.talk("yes", channel="voice")                 # the user's own word, checked in code
    assert hands.confirmed == ["abc123"] and parts == [{"who": "iron_man", "text": "Moved old.txt to the Recycle Bin."}]
    assert "voice" not in brain.pending


def test_anything_but_yes_cancels(cfg, mem):
    hands = FakeHands()
    client = Script(use("ask_team", member="iron_man", task="delete old.txt"), use("delete", path="old.txt"),
                    say("Say yes or no."), say(""))
    brain = make(cfg, mem, client, hands)
    brain.talk("delete old.txt", channel="telegram")
    assert brain.talk("no", channel="telegram") == [{"who": "iron_man", "text": "Okay, I won't do it."}]
    assert hands.cancelled == ["abc123"] and hands.confirmed == []


def test_yes_on_another_channel_does_not_confirm(cfg, mem):
    hands = FakeHands()
    client = Script(use("ask_team", member="iron_man", task="delete old.txt"), use("delete", path="old.txt"),
                    say("Say yes or no."), say(""), say("Yes to what?"))
    brain = make(cfg, mem, client, hands)
    brain.talk("delete old.txt", channel="voice")
    brain.talk("yes", channel="telegram")
    assert hands.confirmed == []


def test_roll_call_everyone_reports(cfg, mem):
    skills = Skills(cfg, mem)
    skills.weather_now = lambda: "It's 4 degrees and light rain in Berlin."
    mem.add_todo("send CV to SEIA")
    brain = Brain(cfg, mem, skills=skills, client=Script(), team=Team(cfg, skills, hands=FakeHands()))
    parts = brain.talk("Hello Jako, wake up!")
    who = [p["who"] for p in parts]
    assert who == ["jako", "iron_man", "black_widow", "hawkeye", "captain_america", "thor", "hulk", "vision", "jako"]
    text = {p["who"]: p["text"] for p in parts}
    assert "LAPTOP-FDG" in text["iron_man"] and "aren't connected" in text["black_widow"]
    assert "1 thing on your to-do list" in text["captain_america"] and "light rain" in text["thor"]
    assert "🦾 Iron Man:" in as_text(parts)


def test_roll_call_survives_offline_laptop(cfg, mem):
    class Offline(FakeHands):
        def status(self):
            return {"error": "offline"}
    skills = Skills(cfg, mem)
    skills.weather_now = lambda: ""
    parts = Team(cfg, skills, hands=Offline()).roll_call()
    assert "can't reach the laptop" in parts[1]["text"]


# ---------------------------------------------------------------- the Windows helper's own rules
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "HOME", tmp_path)
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "notes.txt").write_text("hello")
    return tmp_path


def test_whole_laptop_but_never_secrets_or_system_folders(home):
    assert H.safe_path("Documents/notes.txt") == home / "Documents" / "notes.txt"
    assert H.safe_path("/tmp", must_exist=False)                       # outside the user folder is fine now
    for bad in [".ssh/id_rsa", "Documents/.env", "passwords.txt", "AppData/Local/Google/Chrome/User Data/Default/Login Data"]:
        with pytest.raises(H.Refused):
            H.safe_path(bad, must_exist=False)
    from pathlib import Path
    for bad in [Path("/"), home, Path("/windows/system32/x.dll"), Path("/program files/app/x.exe")]:
        with pytest.raises(H.Refused):
            H.changeable(bad)


def test_actions_run_straight_away_only_catastrophic_commands_wait(home, monkeypatch):
    hands = H.Hands()
    assert hands.do("read_file", {"path": "Documents/notes.txt"}) == {"text": "hello"}
    assert "notes.txt" in hands.do("find_files", {"name": "notes"})["text"]
    monkeypatch.setitem(H.RISKY, "delete", lambda path: f"recycled {path}")
    assert hands.do("delete", {"path": "Documents/notes.txt"}) == {"text": "recycled Documents/notes.txt"}
    monkeypatch.setitem(H.RISKY, "run_command", lambda command: f"ran {command}")
    assert hands.do("run_command", {"command": "Get-Date"}) == {"text": "ran Get-Date"}
    for wreck in ["Format-Volume -DriveLetter D", "format d: /q", "diskpart", "reg delete HKLM\\Software\\X /f",
                  "Remove-Item -Recurse -Force C:\\", "rd /s /q C:\\", "shutdown /s /t 0", "Stop-Computer"]:
        out = hands.do("run_command", {"command": wreck})
        assert "pending" in out, wreck
    assert hands.confirm(out["pending"]) == {"text": "ran Stop-Computer"}
    assert "expired" in hands.confirm(out["pending"])["error"]    # a code works once


def test_opening_a_program_file_runs_it(home, monkeypatch):
    (home / "Downloads").mkdir()
    (home / "Downloads" / "setup.exe").write_bytes(b"MZ")
    started = []
    monkeypatch.setattr(H, "_start", lambda target: started.append(target))
    assert H.Hands().do("open_path", {"path": "Downloads/setup.exe"}) == {"text": "Started setup.exe."}
    assert started and started[0].endswith("setup.exe")


def test_urls_and_app_names_are_checked(home):
    hands = H.Hands()
    assert "error" in hands.do("open_url", {"url": "file:///C:/Windows/System32/cmd.exe"})
    assert "error" in hands.do("open_url", {"url": "javascript:alert(1)"})
    assert "pending" in hands.do("run_command", {"command": "format c:"})
    with pytest.raises(H.Refused):
        H._do_close_app("chrome & del *")


def test_hands_api_needs_the_token(home):
    from fastapi.testclient import TestClient
    client = TestClient(H.create_app("k" * 32))
    assert client.post("/do", json={"action": "list_folder", "args": {}}).status_code == 401
    assert client.post("/do", json={"action": "list_folder", "args": {}},
                       headers={"X-Jako-Token": "wrong"}).status_code == 401
    ok = client.post("/do", json={"action": "list_folder", "args": {}}, headers={"X-Jako-Token": "k" * 32})
    assert "Documents" in ok.json()["text"]
    with pytest.raises(SystemExit):
        H.create_app("short")


def test_hands_starts_without_a_console(home, monkeypatch, tmp_path):
    """pythonw has no stdout/stderr; the helper must still start (this crashed on the real laptop)."""
    import sys
    monkeypatch.setattr(H, "ROOT", tmp_path)
    monkeypatch.setattr(H, "read_token", lambda: "k" * 32)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    def stop(*a, **kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(H, "serve_once", stop)
    with pytest.raises(KeyboardInterrupt):
        H.main()
    assert "laptop link started" in (tmp_path / "logs" / "jako-hands-run.log").read_text()


def test_laptop_link_end_to_end(home, cfg, mem):
    """Jako (server) <- helper polls -> reads a real file -> Iron Man gets the text. Also: wrong token is refused."""
    import socket
    import threading
    import urllib.error
    import uvicorn
    from jako.server import create_app
    from jako.team import HandsClient
    skills = Skills(cfg, mem)
    bridge = HandsClient(cfg)
    brain = SimpleNamespace(cfg=cfg, mem=mem, team=Team(cfg, skills, hands=bridge))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(create_app(brain), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    import time
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    with pytest.raises(urllib.error.HTTPError):
        H.serve_once(H.Hands(), base, "wrong-token-wrong-token", "PC")
    assert bridge.do("read_file", {"path": "x"})["error"].startswith("I can't reach the laptop")
    stop = threading.Event()

    def helper():
        hands = H.Hands()
        while not stop.is_set():
            try:
                H.serve_once(hands, base, cfg.hands_token, "LAPTOP-TEST")
            except Exception:
                time.sleep(0.1)
    threading.Thread(target=helper, daemon=True).start()
    time.sleep(0.5)
    assert bridge.do("read_file", {"path": "Documents/notes.txt"}) == {"text": "hello"}
    assert bridge.status()["computer"] == "LAPTOP-TEST"
    pending = bridge.do("run_command", {"command": "diskpart"})
    assert "pending" in pending
    assert bridge.cancel(pending["pending"]) == {"text": "Cancelled."}
    assert "LAPTOP-TEST" in Team(cfg, skills, hands=bridge)._iron_man()
    stop.set()
    server.should_exit = True


def test_websites_and_store_apps_open(home, monkeypatch):
    opened = []
    monkeypatch.setattr(H, "_start", lambda target: opened.append(target))
    monkeypatch.setattr(H, "_start_menu_shortcut", lambda name: None)
    monkeypatch.setattr(H, "_store_app", lambda name: {"Name": "WhatsApp", "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"}
                        if "whatsapp" in name.lower() else None)
    assert H.open_app("GitHub") == "Opened https://github.com in your browser."
    assert H.open_app("linkedin") == "Opened https://www.linkedin.com in your browser."
    H.open_app("WhatsApp")
    assert opened[-1] in ("whatsapp:", "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App")
    with pytest.raises(H.Refused):
        H.open_app("NoSuchThing")


def test_each_member_only_gets_its_own_laptop_tools(cfg, mem):
    from jako.team import tools_for
    names = {a: {t["name"] for t in tools_for(a)} for a in ("iron_man", "hulk", "vision")}
    assert "delete" not in names["vision"] and "run_command" not in names["vision"] and "screenshot" in names["vision"]
    assert {"disk_report", "old_files", "clean_temp", "performance_report"} <= names["hulk"]
    assert "run_command" not in names["hulk"] and "open_app" in names["iron_man"]
    hands = FakeHands()
    client = Script(use("ask_team", member="vision", task="delete my code"), use("delete", path="x.py"),
                    say("I can only look, not delete."), say(""))
    make(cfg, mem, client, hands).talk("delete x.py")
    assert hands.done == []                              # Vision's delete never reached the laptop


def test_hulk_reads_the_performance_log(cfg, mem, tmp_path):
    from datetime import datetime, timedelta
    from jako.team import performance_report
    rows = ["time,cpu,ram,disk_free_gb,battery,plugged,top_memory_app"]
    now = datetime.now()
    for i, (cpu, ram) in enumerate([(10, 60), (80, 90), (30, 70)]):
        rows.append(f"{(now - timedelta(minutes=30 - i * 10)):%Y-%m-%d %H:%M},{cpu},{ram},120.5,80,1,chrome.exe")
    (cfg.data_dir / "perf.csv").write_text("\n".join(rows) + "\n")
    text = performance_report(cfg.data_dir / "perf.csv", 1)
    assert "CPU average 40%, peak 80%" in text and "above 85% in 1 samples" in text and "chrome.exe" in text
    assert "Hulk here. CPU 30%" in Team(cfg, Skills(cfg, mem), hands=FakeHands())._hulk()


def test_cleanup_runs_when_asked(home, monkeypatch, tmp_path):
    temp = tmp_path / "Temp"
    temp.mkdir()
    old = temp / "old.tmp"
    old.write_bytes(b"x" * 2000)
    import os
    os.utime(old, (0, 0))
    monkeypatch.setattr(H, "_temp_dir", lambda: temp)
    hands = H.Hands()
    out = hands.do("clean_temp", {"days": 7})
    assert "Removed 1 temporary files" in out["text"] and not old.exists()


def test_hawkeye_roll_call_uses_light_queries(cfg, mem):
    from jobhunter.models import Job
    store = Skills(cfg, mem)._store()
    good, thin = Job("ashby", "1", "Flink", "Data Engineer", "u", description="x" * 200), \
        Job("ashby", "2", "Tiny", "Analyst", "u2", description="short")
    good.score, thin.score = 95, 99
    store.save([good, thin])
    skills = Skills(cfg, mem)
    assert skills._store() is skills._store()                # one connection pool
    line = Team(cfg, skills, hands=FakeHands())._hawkeye()
    assert "2 open jobs" in line and "Flink, Data Engineer, score 95" in line   # the job without a real ad isn't 'best'


def test_closed_window_comes_back_unless_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "PAUSE_FLAG", tmp_path / "jako-stopped.flag")
    opened = []
    up = lambda base: True
    assert H.watch_window("http://x", check=lambda: True, opener=opened.append, healthy=up) == "open"
    assert H.watch_window("http://x", check=lambda: False, opener=lambda b: opened.append(b) or True,
                          healthy=up) == "reopened" and opened == ["http://x"]
    assert H.watch_window("http://x", check=lambda: False, opener=opened.append,
                          healthy=lambda b: False) == "jako not running"
    (tmp_path / "jako-stopped.flag").write_text("x")
    assert H.watch_window("http://x", check=lambda: False, opener=opened.append, healthy=up) == "stopped"
    assert opened == ["http://x"]


def test_window_opens_on_localhost_where_the_microphone_is_allowed():
    from jako import hands as H
    assert H.window_url("http://127.0.0.1:8765") == "http://localhost:8765/"
    assert H.window_url("http://localhost:8765/") == "http://localhost:8765/"


def test_team_status_questions_get_the_instant_report():
    from jako.brain import is_roll_call
    for t in ["status of team report", "team status", "what's the team status", "Avengers, status update",
              "give me the team report", "jako wake up"]:
        assert is_roll_call(t), t
    for t in ["remind me to send the status report to my manager tomorrow at nine please ok",
              "open github", "what's the weather"]:
        assert not is_roll_call(t), t


def test_roll_call_never_waits_for_a_stuck_check(cfg, mem):
    import time as _t
    team = Team(cfg, Skills(cfg, mem), hands=FakeHands())
    team.roll_call_seconds = 1
    team._hawkeye = lambda: _t.sleep(30)
    t0 = _t.monotonic()
    parts = team.roll_call()
    assert _t.monotonic() - t0 < 3
    hawk = [p for p in parts if p["who"] == "hawkeye"][0]["text"]
    assert "slow" in hawk


def test_members_run_together_and_a_slow_one_is_not_waited_for(cfg, mem):
    import time as _t
    team = Team(cfg, Skills(cfg, mem), hands=FakeHands())
    team.member_seconds = 1

    def run(client, agent, task, lang, pending, channel="voice"):
        if agent == "black_widow":
            _t.sleep(30)
        _t.sleep(0.3)
        return f"{agent} done"
    team.run = run
    t0 = _t.monotonic()
    out = team.run_many(None, [("hulk", "x"), ("thor", "y"), ("black_widow", "z")], "English", [])
    assert _t.monotonic() - t0 < 2.5
    assert out[0] == "hulk done" and out[1] == "thor done" and "too long" in out[2]


def test_file_search_skips_program_folders_and_stops_on_time(home, monkeypatch):
    (home / "AppData" / "Local" / "x").mkdir(parents=True)
    (home / "AppData" / "Local" / "x" / "team report.txt").write_text("hidden")
    (home / "Documents" / "Team Report.docx").write_text("x")
    out = H.find_files("team report")
    assert "Team Report.docx" in out and "AppData" not in out
    clock = iter([0, 100, 100, 100, 100])                       # the clock jumps past the 8-second limit
    monkeypatch.setattr(H.time, "time", lambda: next(clock, 100))
    assert "stopped after 8 seconds" in H.find_files("nothing-like-this")


def test_jako_does_the_work_himself(cfg, mem):
    client = Script(use("open_app", name="notepad"), use("run_command", command="Get-Date"), say("Done, Nan."))
    hands = FakeHands()
    parts = make(cfg, mem, client, hands).talk("open notepad and tell me the date", channel="voice")
    assert parts == [{"who": "jako", "text": "Done, Nan."}]
    assert [a for a, _ in hands.done] == ["open_app", "run_command"]       # no member in between, no yes needed
    assert len(client.calls) == 3


def test_jako_calls_the_owner_nan_and_thinks_with_opus(tmp_path, monkeypatch):
    from jako import config
    for k in ("JAKO_OWNER_NAME", "JAKO_MODEL", "JAKO_TEAM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "_load_env", lambda: None)
    monkeypatch.setenv("JOBHUNTER_DATA_DIR", str(tmp_path))
    c = config.load()
    assert c.owner_name == "Nan" and c.model == "claude-opus-5-5" and c.team_model == "claude-sonnet-5-5"
    monkeypatch.setenv("JAKO_OWNER_NAME", "Nanda")                          # an old .env setting...
    (tmp_path / "jako-profile.json").write_text('{"owner_name": "Nan", "model": "claude-opus-5-5"}')
    assert config.load().owner_name == "Nan"                                # ...loses to the profile
