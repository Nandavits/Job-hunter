"""WhatsApp on the Windows laptop: send a message, or start a voice or video call.

How it works (fast and reliable, no hunting for buttons):
- Jako keeps Nanda's WhatsApp contacts (name, number) in data/whatsapp-contacts.csv on the laptop. They are added
  by saying e.g. "Jako, save Mahir's WhatsApp number +49 170 1234567" (or by editing that file).
- A message opens the exact chat with  whatsapp://send?phone=<number>&text=<message>  and presses Enter.
- A call opens the chat the same way and presses WhatsApp's own shortcut: Ctrl+Shift+C (voice), Ctrl+Shift+V (video).

Safety: a name must match exactly one saved contact, and keys are only pressed while WhatsApp is the active
window. Otherwise nothing is sent and Jako says what's wrong.
"""
from __future__ import annotations

import csv
import re
import time
import unicodedata
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTACTS = ROOT / "data" / "whatsapp-contacts.csv"


class WhatsAppError(Exception):
    pass


# ---------------------------------------------------------------- contacts
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def match_contact(said: str, names: list[str]) -> tuple[str | None, list[str]]:
    """Pick the one contact that matches what was said. Returns (name, []) or (None, the candidates)."""
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


def clean_number(number: str) -> str:
    """'+49 170 123-4567' -> '491701234567'. Needs the country code."""
    raw = (number or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    elif not raw.startswith("+") and digits.startswith("0"):
        raise WhatsAppError("Please say the number with its country code, like plus 49 for Germany or plus 91 for India.")
    if not 8 <= len(digits) <= 15:
        raise WhatsAppError(f"{number} doesn't look like a phone number.")
    return digits


def load_contacts(path: Path = CONTACTS) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {r["name"].strip(): r["phone"].strip() for r in csv.DictReader(f) if r.get("name") and r.get("phone")}


def save_contact(name: str, number: str, path: Path = CONTACTS) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise WhatsAppError("Which name should I save the number under?")
    phone = clean_number(number)
    contacts = {n: p for n, p in load_contacts(path).items() if _norm(n) != _norm(name)}
    contacts[name] = phone
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "phone"])
        for n, p in sorted(contacts.items(), key=lambda x: x[0].lower()):
            w.writerow([n, p])
    return f"Saved {name}'s WhatsApp number (+{phone})."


def list_contacts(path: Path = CONTACTS) -> str:
    c = load_contacts(path)
    if not c:
        return "No WhatsApp contacts saved yet. Say for example: save Mahir's WhatsApp number plus 49 170 1234567."
    return "WhatsApp contacts: " + ", ".join(sorted(c, key=str.lower)) + "."


def find(contact: str, path: Path = CONTACTS) -> tuple[str, str]:
    contacts = load_contacts(path)
    name, options = match_contact(contact, list(contacts))
    if name:
        return name, contacts[name]
    if options:
        raise WhatsAppError(f"I have more than one {contact}: {', '.join(options)}. Which one do you mean?")
    raise WhatsAppError(f"I don't have a WhatsApp number for {contact} yet. Say: save {contact}'s WhatsApp number, "
                        "and the number with its country code.")


# ---------------------------------------------------------------- the app
def _foreground_title() -> str:
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _bring_whatsapp_forward() -> bool:
    import ctypes
    hwnd = ctypes.windll.user32.FindWindowW(None, "WhatsApp")
    if not hwnd:
        return False
    try:
        from pywinauto.controls.hwndwrapper import HwndWrapper
        HwndWrapper(hwnd).set_focus()
    except Exception:
        ctypes.windll.user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    return True


def _open_chat(phone: str, text: str = "") -> None:
    import os
    url = f"whatsapp://send?phone={phone}"
    if text:
        url += "&text=" + urllib.parse.quote(text)
    os.startfile(url)                                       # nosec - fixed scheme, the number is digits only
    end = time.time() + 25
    while time.time() < end:                                # wait until WhatsApp is the active window
        if _foreground_title() == "WhatsApp":
            time.sleep(2.5)                                 # let the chat open
            if _foreground_title() == "WhatsApp":
                return
        else:
            _bring_whatsapp_forward()
        time.sleep(0.7)
    raise WhatsAppError("WhatsApp didn't come to the front, so I didn't press anything. Is WhatsApp signed in?")


def _press(keys: str) -> None:
    from pywinauto.keyboard import send_keys
    if _foreground_title() != "WhatsApp":                   # never type into another app
        raise WhatsAppError("WhatsApp lost focus, so I didn't press anything.")
    send_keys(keys)


# ---------------------------------------------------------------- actions
def send_message(contact: str, text: str) -> str:
    if not (text or "").strip():
        raise WhatsAppError("There's no message text to send.")
    name, phone = find(contact)
    _open_chat(phone, text.strip())
    _press("{ENTER}")
    return f"Sent to {name} on WhatsApp: “{text.strip()}”"


def call(contact: str, video: bool = False) -> str:
    name, phone = find(contact)
    _open_chat(phone)
    _press("^+v" if video else "^+c")
    return f"Calling {name} on WhatsApp ({'video' if video else 'voice'} call)."
