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
    assert [t["name"] for t in client.calls[0]["tools"]] == ["remember", "forget", "ask_team"]
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
    assert who == ["jako", "iron_man", "black_widow", "hawkeye", "captain_america", "thor", "jako"]
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


def test_paths_stay_inside_home_and_out_of_secrets(home):
    assert H.safe_path("Documents/notes.txt") == home / "Documents" / "notes.txt"
    for bad in ["/etc/passwd", "../outside.txt", ".ssh/id_rsa", "Documents/.env", "passwords.txt",
                "AppData/Local/Google/Chrome/User Data/Default/Login Data"]:
        with pytest.raises(H.Refused):
            H.safe_path(bad, must_exist=False)


def test_safe_actions_run_and_risky_ones_wait(home, monkeypatch):
    hands = H.Hands()
    assert hands.do("read_file", {"path": "Documents/notes.txt"}) == {"text": "hello"}
    assert "notes.txt" in hands.do("find_files", {"name": "notes"})["text"]
    out = hands.do("delete", {"path": "Documents/notes.txt"})
    assert "pending" in out and "Recycle Bin" in out["text"]
    assert (home / "Documents" / "notes.txt").exists()            # nothing happened yet
    assert "error" in hands.do("delete", {"path": "/etc/hosts"})  # refused before even asking
    monkeypatch.setitem(H.RISKY, "delete", lambda path: f"recycled {path}")
    assert hands.confirm(out["pending"]) == {"text": "recycled Documents/notes.txt"}
    assert "expired" in hands.confirm(out["pending"])["error"]    # a code works once


def test_opening_a_program_file_needs_yes(home):
    (home / "Downloads").mkdir()
    (home / "Downloads" / "setup.exe").write_bytes(b"MZ")
    out = H.Hands().do("open_path", {"path": "Downloads/setup.exe"})
    assert "pending" in out and "start the program" in out["text"]


def test_urls_and_app_names_are_checked(home):
    hands = H.Hands()
    assert "error" in hands.do("open_url", {"url": "file:///C:/Windows/System32/cmd.exe"})
    assert "error" in hands.do("open_url", {"url": "javascript:alert(1)"})
    assert "pending" in hands.do("run_command", {"command": "Get-Date"})
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
