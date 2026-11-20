"""WhatsApp calls and messages: right away, but only to exactly one matching chat."""
from jako import hands as H
from jako import whatsapp as W
from jako.brain import YES


def test_one_clear_match_is_used():
    chats = ["Mahir Khan", "Meghana", "Mom", "Data Team Berlin"]
    assert W.match_contact("Meghana", chats) == ("Meghana", [])
    assert W.match_contact("mahir", chats) == ("Mahir Khan", [])
    assert W.match_contact("data team", chats) == ("Data Team Berlin", [])
    assert W.match_contact("Méghana", chats) == ("Meghana", [])          # accents don't matter


def test_unclear_or_unknown_names_do_nothing():
    name, options = W.match_contact("Mahir", ["Mahir Khan", "Mahir S", "Mom"])
    assert name is None and options == ["Mahir Khan", "Mahir S"]
    assert W.match_contact("Ravi", ["Mahir Khan", "Mom"]) == (None, [])
    assert W.match_contact("", ["Mom"]) == (None, [])
    assert W.match_contact("Mahir", ["Mahir", "Mahir Khan"]) == ("Mahir", [])   # exact name wins


def test_contacts_are_saved_and_found(tmp_path):
    f = tmp_path / "c.dat"
    assert W.save_contact("Mahir Khan", "+49 170 123-4567", f) == "Saved Mahir Khan's WhatsApp number (+49 ••• ••67), encrypted on this laptop."
    W.save_contact("Meghana", "0091 98765 43210", f)
    assert W.find("mahir", f) == ("Mahir Khan", "491701234567")
    assert W.find("Meghana", f) == ("Meghana", "919876543210")
    W.save_contact("mahir khan", "+49 170 9999999", f)                    # same name: updated, not doubled
    assert W.load_contacts(f) == {"mahir khan": "491709999999", "Meghana": "919876543210"}
    assert "Meghana" in W.list_contacts(f)


def test_unknown_or_unclear_contacts_send_nothing(tmp_path):
    import pytest
    f = tmp_path / "c.dat"
    W.save_contact("Mahir Khan", "+491701234567", f)
    W.save_contact("Mahir S", "+491709876543", f)
    with pytest.raises(W.WhatsAppError, match="more than one"):
        W.find("Mahir", f)
    with pytest.raises(W.WhatsAppError, match="don't have a WhatsApp number for Ravi"):
        W.find("Ravi", f)


def test_numbers_need_a_country_code():
    import pytest
    assert W.clean_number("+91 98765-43210") == "919876543210"
    with pytest.raises(W.WhatsAppError, match="country code"):
        W.clean_number("0170 1234567")
    with pytest.raises(W.WhatsAppError, match="doesn't look like"):
        W.clean_number("+12")


def test_message_and_call_press_keys_only_in_whatsapp(tmp_path, monkeypatch):
    import pytest
    f = tmp_path / "c.dat"
    W.save_contact("Meghana", "+919876543210", f)
    monkeypatch.setattr(W, "CONTACTS", f)
    monkeypatch.setattr(W.find, "__defaults__", (f,))
    opened, pressed = [], []
    monkeypatch.setattr(W, "_open_chat", lambda phone, text="": opened.append((phone, text)))
    title = {"t": "WhatsApp"}
    monkeypatch.setattr(W, "_foreground_title", lambda: title["t"])
    import types, sys
    fake = types.ModuleType("pywinauto.keyboard"); fake.send_keys = lambda k: pressed.append(k)
    monkeypatch.setitem(sys.modules, "pywinauto", types.ModuleType("pywinauto"))
    monkeypatch.setitem(sys.modules, "pywinauto.keyboard", fake)
    assert W.send_message("meghana", "Hi, on my way!") == "Sent to Meghana on WhatsApp: “Hi, on my way!”"
    assert W.call("Meghana", video=True).startswith("Calling Meghana")
    assert opened == [("919876543210", "Hi, on my way!"), ("919876543210", "")] and pressed == ["{ENTER}", "^+v"]
    title["t"] = "Notepad"                                                 # focus elsewhere: press nothing
    with pytest.raises(W.WhatsAppError, match="didn't press anything"):
        W.call("Meghana")
    assert pressed == ["{ENTER}", "^+v"]


def test_whatsapp_and_closing_apps_need_no_yes(monkeypatch):
    sent = []
    monkeypatch.setattr(W, "send_message", lambda c, t: sent.append((c, t)) or f"Sent to {c} on WhatsApp: “{t}”")
    monkeypatch.setattr(W, "call", lambda c, v=False: f"Calling {c} on WhatsApp ({'video' if v else 'voice'} call).")
    monkeypatch.setattr(H, "_do_close_app", lambda name: f"Closed {name}.")
    monkeypatch.setitem(H.SAFE, "close_app", H._do_close_app)
    h = H.Hands()
    assert h.do("whatsapp_message", {"contact": "Meghana", "text": "Hi"}) == {"text": "Sent to Meghana on WhatsApp: “Hi”"}
    assert h.do("whatsapp_call", {"contact": "Mahir", "video": True})["text"].startswith("Calling Mahir")
    assert h.do("close_app", {"name": "msedge"}) == {"text": "Closed msedge."}
    assert sent == [("Meghana", "Hi")] and h.pending == {}
    assert "pending" in h.do("delete", {"path": "~/x.txt"}) or "error" in h.do("delete", {"path": "~/x.txt"})


def test_whatsapp_problems_are_said_not_crashed(monkeypatch):
    def unclear(c, t):
        raise W.WhatsAppError("I found more than one chat for Mahir: Mahir Khan, Mahir S. Which one do you mean?")
    monkeypatch.setattr(W, "send_message", unclear)
    out = H.Hands().do("whatsapp_message", {"contact": "Mahir", "text": "Hi"})
    assert "Which one do you mean" in out["text"]


def test_iron_man_has_the_whatsapp_tools():
    from jako.team import HANDS_TOOLS, LAPTOP_TOOLS
    assert {"whatsapp_message", "whatsapp_call", "whatsapp_save_contact", "whatsapp_contacts"} <= set(HANDS_TOOLS["iron_man"])
    names = {t["name"] for t in LAPTOP_TOOLS}
    assert {"whatsapp_message", "whatsapp_call"} <= names


def test_natural_yes_answers():
    ok = lambda t: bool(YES.match(t)) and len(t.split()) <= 8        # noqa: E731 - same rule as Jako's gate
    for t in ["yes", "yes close it", "Yes, do it", "ok do it", "sure go ahead", "ja mach das", "go ahead"]:
        assert ok(t), t
    for t in ["no", "yes but don't close it", "please", "not now", "ok no wait",
              "yes and then also tell me about the weather in Berlin tomorrow please"]:
        assert not ok(t), t


def test_numbers_are_masked():
    assert W.mask("491701234567") == "+49 ••• ••67"
    assert W.mask_numbers("call +49 170 1234567 or 0091 98765 43210 at 18:00 on 2026-11-20") == \
        "call +49 ••• ••67 or +00 ••• ••10 at 18:00 on 2026-11-20"


def test_contacts_file_is_not_plain_text(tmp_path):
    f = tmp_path / "c.dat"
    W.save_contact("Mahir", "+491701234567", f)
    raw = f.read_bytes()
    assert b"491701234567" not in raw and b"Mahir" not in raw              # encrypted on Windows, encoded elsewhere
    assert W.load_contacts(f) == {"Mahir": "491701234567"}
    assert "491701234567" not in W.list_contacts(f) and "+49 ••• ••67" in W.list_contacts(f)
    assert W.forget_contact("mahir", f) == "Deleted Mahir from your WhatsApp contacts." and W.load_contacts(f) == {}


def test_an_old_plain_list_moves_into_the_encrypted_file(tmp_path):
    (tmp_path / "c.csv").write_text("name,phone\nMom,919876543210\n")
    f = tmp_path / "c.dat"
    assert W.load_contacts(f) == {"Mom": "919876543210"}
    assert not (tmp_path / "c.csv").exists()


def test_saving_a_contact_never_goes_to_the_ai(tmp_path):
    from types import SimpleNamespace
    from jako.brain import Brain, save_contact_request
    from jako.config import JakoConfig
    from jako.memory import Memory
    assert save_contact_request("Jako, save Mahir's WhatsApp number plus 49 170 1234567") == ("Mahir", "+49 170 1234567")
    assert save_contact_request("save my mom's number +91 98765 43210") == ("mom", "+91 98765 43210")
    assert save_contact_request("save number +49 170 1234567 as Mahir Khan") == ("Mahir Khan", "+49 170 1234567")
    assert save_contact_request("save the file to my desktop") is None

    class Hands:
        def __init__(self): self.calls = []
        def do(self, action, args):
            self.calls.append((action, args)); return {"text": "Saved Mahir's WhatsApp number (+49 ••• ••67), encrypted on this laptop."}

    class NoAI:
        class messages:
            @staticmethod
            def create(**kw):
                raise AssertionError("the AI must not be asked")
    cfg = JakoConfig(api_key="t", data_dir=tmp_path, hands_token="t" * 32)
    mem = Memory(tmp_path / "j.db")
    hands = Hands()
    b = Brain(cfg, mem, client=NoAI(), team=SimpleNamespace(hands=hands))
    parts = b.talk("save Mahir's WhatsApp number plus 49 170 1234567", channel="voice")
    assert parts == [{"who": "iron_man", "text": "Saved Mahir's WhatsApp number (+49 ••• ••67), encrypted on this laptop."}]
    assert hands.calls == [("whatsapp_save_contact", {"name": "Mahir", "number": "+49 170 1234567"})]
    stored = str(mem.history("voice"))
    assert "1234567" not in stored and "+49 ••• ••67" in stored


def test_old_numbers_in_the_conversation_are_masked_on_start(tmp_path):
    import json, sqlite3
    from jako.memory import Memory
    Memory(tmp_path / "j.db")
    db = sqlite3.connect(tmp_path / "j.db")
    db.execute("insert into messages (channel, role, content, at) values ('voice', 'user', ?, '2026-11-20T19:25:00')",
               (json.dumps("save Mahir's number +49 170 1234567"),))
    db.commit()
    mem = Memory(tmp_path / "j.db")
    assert "1234567" not in str(mem.history("voice")) and "+49 ••• ••67" in str(mem.history("voice"))


def test_file_tools_never_open_the_contacts_or_voiceprint(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(H, "HOME", tmp_path)
    for name in ("whatsapp-contacts.dat", "whatsapp-contacts.csv", "voiceprint.json"):
        (tmp_path / name).write_text("x")
        with pytest.raises(H.Refused):
            H.safe_path(str(tmp_path / name))
