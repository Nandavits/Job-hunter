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
