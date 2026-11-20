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


def test_special_characters_are_typed_literally():
    assert W._escape_keys("Hi :) 50% (ok) {x} a+b ~") == "Hi :{)} 50{%} {(}ok{)} {{}x{}} a{+}b {~}"
    assert W._escape_keys("line1\nline2") == "line1+{ENTER}line2"


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
    assert {"whatsapp_message", "whatsapp_call"} <= set(HANDS_TOOLS["iron_man"])
    names = {t["name"] for t in LAPTOP_TOOLS}
    assert {"whatsapp_message", "whatsapp_call"} <= names


def test_natural_yes_answers():
    ok = lambda t: bool(YES.match(t)) and len(t.split()) <= 8        # noqa: E731 - same rule as Jako's gate
    for t in ["yes", "yes close it", "Yes, do it", "ok do it", "sure go ahead", "ja mach das", "go ahead"]:
        assert ok(t), t
    for t in ["no", "yes but don't close it", "please", "not now", "ok no wait",
              "yes and then also tell me about the weather in Berlin tomorrow please"]:
        assert not ok(t), t
