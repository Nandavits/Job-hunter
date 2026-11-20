"""WhatsApp on the Windows laptop: send a message, or start a voice or video call.

Drives the WhatsApp desktop app through Windows UI Automation (pywinauto), the same way a screen reader
would: it finds the search box, the chat list, the message box and the call buttons by their names.

Safety: it only acts when exactly one chat matches the name that was said. If no chat or several chats match
("Mahir Khan" and "Mahir S"), it does nothing and says which names it found, so a misheard name never
reaches the wrong person.
"""
from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path

SEARCH_NAMES = ("search or start a new chat", "search input textbox", "search", "suchen")
MESSAGE_NAMES = ("type a message", "type a message to", "message", "nachricht schreiben", "nachricht")
VOICE_NAMES = ("voice call", "audio call", "sprachanruf", "anruf")
VIDEO_NAMES = ("video call", "videoanruf")
CALL_MENU_NAMES = ("call", "calls", "anrufen")


class WhatsAppError(Exception):
    pass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def match_contact(said: str, names: list[str]) -> tuple[str | None, list[str]]:
    """Pick the one chat that matches what was said. Returns (name, []) or (None, the candidates)."""
    want = _norm(said)
    if not want:
        return None, []
    names = list(dict.fromkeys(n for n in names if n and n.strip()))
    exact = [n for n in names if _norm(n) == want]
    if len(exact) == 1:
        return exact[0], []
    words = want.split()
    starts = [n for n in names if all(any(w2.startswith(w) for w2 in _norm(n).split()) for w in words)]
    if len(starts) == 1:
        return starts[0], []
    return None, (exact or starts)[:5]


def _escape_keys(text: str) -> str:
    """pywinauto type_keys treats + ^ % ~ ( ) { } [ ] as special keys."""
    return re.sub(r"([+^%~(){}\[\]])", r"{\1}", text).replace("\n", "+{ENTER}")


# ---------------------------------------------------------------- the app window
def _window(timeout: float = 20):
    from pywinauto import Desktop
    from pywinauto.timings import wait_until_passes
    def find():
        for w in Desktop(backend="uia").windows():
            title = (w.window_text() or "").strip()
            if title == "WhatsApp" or title.startswith("WhatsApp"):
                return w
        raise WhatsAppError("WhatsApp window not found")
    return wait_until_passes(timeout, 0.5, find)


def _start_whatsapp():
    from .hands import open_app
    open_app("whatsapp")
    w = _window()
    try:
        if w.is_minimized():
            w.restore()
        w.set_focus()
    except Exception:
        pass
    time.sleep(1.0)
    return w


def _find(w, names: tuple, control_type: str = "Button", timeout: float = 6):
    end = time.time() + timeout
    while time.time() < end:
        for el in w.descendants(control_type=control_type):        # filtered by Windows: fast
            n = _norm(el.window_text())
            if n and any(n == x or n.startswith(x) for x in names):
                return el
        time.sleep(0.4)
    return None


def _open_chat(w, contact: str) -> str:
    box = _find(w, SEARCH_NAMES, "Edit")
    if box is None:
        raise WhatsAppError("I couldn't find WhatsApp's search box.")
    box.click_input()
    box.type_keys("^a{BACKSPACE}", set_foreground=False)
    box.type_keys(_escape_keys(contact), with_spaces=True, set_foreground=False)
    time.sleep(1.8)                                          # let WhatsApp filter the chat list
    items = [el for el in w.descendants(control_type="ListItem") if (el.window_text() or "").strip()]
    # a list item's text often holds "Name, last message, time": the chat name comes first
    names = {}
    for el in items:
        first = re.split(r"[\n,]", el.window_text().strip())[0].strip()
        names.setdefault(first, el)
    name, candidates = match_contact(contact, list(names))
    if name is None:
        box.type_keys("^a{BACKSPACE}", set_foreground=False)
        if candidates:
            raise WhatsAppError(f"I found more than one chat for {contact}: {', '.join(candidates)}. Which one do you mean?")
        raise WhatsAppError(f"I couldn't find a WhatsApp chat called {contact}.")
    names[name].click_input()
    time.sleep(1.2)
    return name


# ---------------------------------------------------------------- actions
def send_message(contact: str, text: str) -> str:
    if not text.strip():
        raise WhatsAppError("There's no message text to send.")
    w = _start_whatsapp()
    name = _open_chat(w, contact)
    box = _find(w, MESSAGE_NAMES, "Edit")
    if box is None:
        raise WhatsAppError(f"I opened the chat with {name} but couldn't find the message box, so nothing was sent.")
    box.click_input()
    box.type_keys(_escape_keys(text), with_spaces=True, set_foreground=False)
    time.sleep(0.3)
    box.type_keys("{ENTER}", set_foreground=False)
    return f"Sent to {name} on WhatsApp: “{text}”"


def call(contact: str, video: bool = False) -> str:
    w = _start_whatsapp()
    name = _open_chat(w, contact)
    names = VIDEO_NAMES if video else VOICE_NAMES
    btn = _find(w, names, "Button", timeout=3)
    if btn is None:                                          # newer WhatsApp: one "Call" button with a small menu
        menu = _find(w, CALL_MENU_NAMES, "Button", timeout=3)
        if menu is not None:
            menu.click_input()
            time.sleep(0.8)
            btn = _find(_window(), names, "MenuItem", timeout=3) or _find(_window(), names, "Button", timeout=2)
    if btn is None:
        raise WhatsAppError(f"I opened the chat with {name} but couldn't find the {'video' if video else 'voice'} call button.")
    btn.click_input()
    return f"Calling {name} on WhatsApp ({'video' if video else 'voice'} call)."


# ---------------------------------------------------------------- one-time look at WhatsApp's controls
def inspect(out: Path) -> None:
    """Write the names of WhatsApp's buttons and boxes to a file (chat names and messages are hidden)."""
    lines: list[str] = []
    def note(s):
        lines.append(s)
        out.write_text("\n".join(lines), encoding="utf-8")         # saved as it goes
    t0 = time.time()
    note("starting")
    w = _start_whatsapp()
    note(f"window: {w.window_text()!r} after {time.time() - t0:.1f}s")
    def dump(when):
        note(f"--- {when}")
        for ct in ("Edit", "Button", "MenuItem"):
            t = time.time()
            els = w.descendants(control_type=ct)
            note(f"{ct}: {len(els)} found in {time.time() - t:.1f}s")
            for el in els[:80]:
                note(f"  {ct:8} {(el.window_text() or '')[:60]!r} id={el.element_info.automation_id!r}")
        t = time.time()
        items = w.descendants(control_type="ListItem")
        note(f"ListItem: {len(items)} found in {time.time() - t:.1f}s (text hidden: lengths only)")
        note("  " + ", ".join(str(len(el.window_text() or "")) for el in items[:30]))
        return items
    items = dump("main window")
    first = next((el for el in items if el.window_text()), None)
    if first is not None:                                   # open the top chat (nothing is sent) to see its buttons
        first.click_input()
        time.sleep(1.5)
        dump("inside a chat")
    note(f"done in {time.time() - t0:.1f}s")
