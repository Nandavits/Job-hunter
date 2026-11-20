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
    f = tmp_path / "c.csv"
    assert W.save_contact("Mahir Khan", "+49 170 123-4567", f) == "Saved Mahir Khan's WhatsApp number (+491701234567)."
    W.save_contact("Meghana", "0091 98765 43210", f)
    assert W.find("mahir", f) == ("Mahir Khan", "491701234567")
    assert W.find("Meghana", f) == ("Meghana", "919876543210")
    W.save_contact("mahir khan", "+49 170 9999999", f)                    # same name: updated, not doubled
    assert W.load_contacts(f) == {"mahir khan": "491709999999", "Meghana": "919876543210"}
    assert "Meghana" in W.list_contacts(f)


def test_unknown_or_unclear_contacts_send_nothing(tmp_path):
    import pytest
    f = tmp_path / "c.csv"
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
    f = tmp_path / "c.csv"
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
