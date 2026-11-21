"""Jako always answers: long jobs continue in the background; the laptop helper never blocks; Vision edits code."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from jako import hands as H
from jako.brain import Brain
from jako.config import JakoConfig
from jako.memory import Memory


@pytest.fixture
def brain(tmp_path):
    cfg = JakoConfig(api_key="t", data_dir=tmp_path, hands_token="t" * 32)
    return Brain(cfg, Memory(tmp_path / "j.db"), team=SimpleNamespace(hands=None))


def test_quick_answers_come_straight_back(brain, monkeypatch):
    monkeypatch.setattr(brain, "talk", lambda text, channel="voice": [{"who": "thor", "text": "Sunny."}])
    assert brain.talk_soon("weather?", wait=2) == [{"who": "thor", "text": "Sunny."}]
    assert brain.jobs == {}


def test_slow_jobs_continue_and_jako_keeps_answering(brain, monkeypatch):
    gate = threading.Event()

    def talk(text, channel="voice"):
        if "fix" in text:
            gate.wait(5)
            return [{"who": "vision", "text": "I fixed the call button and restarted Jako."}]
        return [{"who": "jako", "text": "Hi!"}]
    monkeypatch.setattr(brain, "talk", talk)
    t0 = time.time()
    first = brain.talk_soon("fix the whatsapp call", wait=0.3)
    assert time.time() - t0 < 2 and "I'm on it" in first[0]["text"]
    assert brain.talk_soon("hello", wait=2) == [{"who": "jako", "text": "Hi!"}]          # still answering
    doing = brain.talk_soon("Jako, what are you doing?", wait=2)
    assert "fix the whatsapp call" in doing[0]["text"]
    gate.set()
    time.sleep(0.5)
    assert brain.mem.take_outbox() == ["[vision] I fixed the call button and restarted Jako."]
    assert "Nothing is running" in brain.talk_soon("are you busy", wait=2)[0]["text"]


def test_helper_keeps_polling_while_a_slow_job_runs():
    from concurrent.futures import ThreadPoolExecutor
    release, posted = threading.Event(), []

    class SlowHands(H.Hands):
        def do(self, action, args):
            release.wait(5)
            return {"text": "done"}

    jobs = [{"id": "1", "kind": "do", "action": "whatsapp_call", "args": {}}, {"id": "2", "kind": "status"}]

    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body

    def opener(req, timeout=0):
        if req.full_url.endswith("/api/hands/result"):
            posted.append(json.loads(req.data)["id"])
            return Resp(b"{}")
        return Resp(json.dumps(jobs.pop(0) if jobs else {}).encode())

    pool = ThreadPoolExecutor(2)
    h = SlowHands()
    assert H.serve_once(h, "http://x", "t", "PC", opener=opener, pool=pool)
    assert H.serve_once(h, "http://x", "t", "PC", opener=opener, pool=pool)
    time.sleep(0.3)
    assert posted == ["2"]                     # the status answer went out while the slow call was still running
    release.set()
    pool.shutdown(wait=True)
    assert posted == ["2", "1"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "jako").mkdir()
    (tmp_path / "jako" / "team.py").write_text("A = 1\nB = 2\n")
    (tmp_path / "start.bat").write_text("@echo off\r\necho hi\r\n")
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "jako.db").write_text("x")
    monkeypatch.setattr(H, "PROJECT", tmp_path)
    monkeypatch.setattr(H, "CODE_BACKUPS", tmp_path / "data" / "code-backups")
    return tmp_path


def test_vision_reads_long_files_in_parts(project):
    (project / "jako" / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(1, 501)) + "\n")
    out = H.code_read("jako/big.py", start=1, lines=200)
    assert "lines 1-200 of 500" in out and "start=201" in out and "  200| x200 = 200" in out
    assert "lines 401-500 of 500" in H.code_read("jako/big.py", start=401, lines=200)
    assert "jako/team.py:2: B = 2" in H.code_search("B = 2")


def test_vision_edits_with_backup_and_undo(project):
    assert H.code_edit("jako/team.py", "B = 2", "B = 3").startswith("Edited jako/team.py")
    assert (project / "jako" / "team.py").read_text() == "A = 1\nB = 3\n"
    assert "Not changed" in H.code_edit("jako/team.py", "B = 3", "B = (")          # broken Python is refused
    assert (project / "jako" / "team.py").read_text() == "A = 1\nB = 3\n"
    assert "appears 0 times" in H.code_edit("jako/team.py", "C = 9", "C = 1")
    assert H.code_undo().startswith("Put jako/team.py back")
    assert (project / "jako" / "team.py").read_text() == "A = 1\nB = 2\n"
    H.code_edit("start.bat", "echo hi", "echo hello")
    assert (project / "start.bat").read_bytes() == b"@echo off\r\necho hello\r\n"   # batch files keep CRLF
    H.code_write("jako/new.py", "C = 1\n")
    assert H.code_undo() == "Removed the new file jako/new.py."


def test_vision_stays_out_of_secrets_and_data(project):
    for bad in (".env", "data/jako.db", "../outside.py", ".git/config"):
        with pytest.raises(H.Refused):
            H._code_path(bad)
    listing = H.code_list()
    assert "jako/team.py" in listing and ".env" not in listing and "data/" not in listing


def test_vision_has_the_engineer_tools():
    from jako.team import HANDS_TOOLS, LAPTOP_TOOLS
    need = {"code_list", "code_read", "code_search", "code_edit", "code_write", "code_undo", "restart_jako"}
    assert need <= set(HANDS_TOOLS["vision"]) and need <= {t["name"] for t in LAPTOP_TOOLS} and need <= set(H.SAFE)


def test_closing_edge_keeps_jakos_window(monkeypatch):
    import psutil

    class P:
        def __init__(self, name, cmd):
            self.info, self.done = {"name": name, "cmdline": cmd}, False
        def terminate(self):
            self.done = True
    browser, jako = P("msedge.exe", ["msedge.exe"]), P("msedge.exe", ["msedge.exe", "--user-data-dir=C:\\\\job-hunter\\\\data\\\\jako-window"])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [browser, jako])
    assert "Jako's own window stays open" in H._do_close_app("msedge")
    assert browser.done and not jako.done


def test_stop_ends_background_work(brain, monkeypatch):
    gate = threading.Event()
    seen = {}

    def talk(text, channel="voice"):
        gen = brain._stop_gen
        gate.wait(5)
        seen["stopped"] = brain._stop_gen != gen
        return [{"who": "jako", "text": "half done"}]
    monkeypatch.setattr(brain, "talk", talk)
    brain.talk_soon("fill in the form on the website", wait=0.2)
    assert brain.talk_soon("Jako, stop", wait=2)[0]["text"] == "Stopped 1 job."
    gate.set()
    time.sleep(0.4)
    assert seen["stopped"] and brain.mem.take_outbox() == []              # nothing half-finished is announced
    assert brain.talk_soon("cancel that", wait=2)[0]["text"] == "Nothing was running."
    from jako.brain import STOP_TASK
    assert not STOP_TASK.match("stop the music in spotify and play jazz")


def test_old_screenshots_are_dropped():
    from jako.team import prune_images
    shot = lambda i: {"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(i), "content": [
        {"type": "text", "text": f"shot {i}"}, {"type": "image", "source": {"type": "base64", "data": "x"}}]}]}
    msgs = [{"role": "user", "content": "go"}] + [shot(i) for i in range(6)]
    out = prune_images(msgs, keep=3)
    kinds = [c["type"] for m in out[1:] for c in m["content"][0]["content"]]
    assert kinds.count("image") == 3 and out[-1]["content"][0]["content"][1]["type"] == "image"
    assert out[1]["content"][0]["content"][1] == {"type": "text", "text": "[older screenshot removed]"}


def test_jako_sends_email_with_a_cv(tmp_path, monkeypatch):
    import smtplib
    from jako.skills import Skills
    cfg = JakoConfig(api_key="t", data_dir=tmp_path / "data", hands_token="t" * 32,
                     gmail_address="nan@gmail.com", gmail_password="app-pass")
    (tmp_path / "data").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "CV_Nan.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "data" / ".env").write_text("SECRET")
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=0): sent["host"] = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, user, pw): sent["login"] = user
        def send_message(self, msg): sent["msg"] = msg
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    s = Skills(cfg, Memory(tmp_path / "data" / "j.db"))
    out = s.send_email("hr@company.de", "Application", "Hello", attachments=["C:\\\\Users\\\\HP\\\\Documents\\\\job-hunter\\\\output\\\\CV_Nan.pdf"])
    assert out == "Sent to hr@company.de: “Application” with CV_Nan.pdf."
    assert sent["host"] == "smtp.gmail.com" and sent["login"] == "nan@gmail.com"
    assert [p.get_filename() for p in sent["msg"].iter_attachments()] == ["CV_Nan.pdf"]
    assert "only" in s.run("send_email", {"to": "a@b.de", "subject": "x", "body": "y", "attachments": ["data/.env"]})
    assert "check the address" in s.send_email("not-an-address", "x", "y")


def test_jako_saves_calendar_and_telegram_settings(tmp_path):
    from jako.skills import Skills
    cfg = JakoConfig(api_key="t", data_dir=tmp_path, hands_token="t" * 32)
    s = Skills(cfg, Memory(tmp_path / "j.db"))
    url = "https://calendar.google.com/calendar/ical/nan%40gmail.com/private-abc123/basic.ics"
    assert s.save_setting("calendar_ics_url", url) == "Setting calendar_ics_url: saved (hidden)."
    assert cfg.calendar_ics_url == url                                   # works straight away
    assert "isn't a Google Calendar" in s.save_setting("calendar_ics_url", "https://evil.example/x.ics")
    assert "restart_jako" in s.save_setting("telegram_token", "123456789:AAH" + "x" * 32)
    assert s.save_setting("telegram_chat_id", "987654321").startswith("Setting telegram_chat_id: 987654321.")
    assert "can't change" in s.save_setting("gmail_password", "x")
    profile = json.loads((tmp_path / "jako-profile.json").read_text())
    assert profile["calendar_ics_url"] == url and profile["telegram_chat_id"] == "987654321"


def test_calendar_events_open_filled_in_for_the_right_account(tmp_path, monkeypatch):
    import urllib.parse
    opened = []
    monkeypatch.setattr(H, "_start", lambda target: opened.append(target))
    monkeypatch.setattr(H, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "jako-profile.json").write_text(json.dumps(
        {"calendar_ics_url": "https://calendar.google.com/calendar/ical/nandavits586%40gmail.com/private-x/basic.ics"}))
    out = H.Hands().do("calendar_add", {"title": "Interview Flink", "start": "2026-11-24 14:00", "location": "Teams"})
    assert "click Save" in out["text"] and "Tue 24 Nov 2026, 14:00-15:00" in out["text"]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(opened[-1]).query)
    assert q["dates"] == ["20261124T140000/20261124T150000"] and q["text"] == ["Interview Flink"]
    assert q["authuser"] == ["nandavits586@gmail.com"] and q["ctz"] == ["Europe/Berlin"]
    H.calendar_add("Holiday", "2026-12-24", all_day=True)
    assert "dates=20261224%2F20261225" in opened[-1]
    assert "Tue 24 Nov 2026" in H.calendar_open("2026-11-24") and opened[-1].startswith(
        "https://calendar.google.com/calendar/r/day/2026/11/24?authuser=nandavits586@gmail.com")
