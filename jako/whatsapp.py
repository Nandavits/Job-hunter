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

import base64
import csv
import json
import os
import re
import time
import unicodedata
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTACTS = ROOT / "data" / "whatsapp-contacts.dat"          # encrypted with Windows DPAPI (this user, this laptop)
OLD_CSV = ROOT / "data" / "whatsapp-contacts.csv"            # earlier plain file: moved into the encrypted one


# ---------------------------------------------------------------- privacy
def mask(phone: str) -> str:
    """'491701234567' -> '+49 ••• ••67': country code and the last two digits only."""
    d = re.sub(r"\D", "", phone or "")
    if len(d) < 6:
        return "•••"
    return f"+{d[:2]} ••• ••{d[-2:]}"


PHONE_RE = re.compile(r"(?<![\w])(?:\+|00)?\d[\d \-/().]{6,}\d")


def mask_numbers(text: str) -> str:
    """Hide every phone-like number in a text (for logs, memory and anything sent to the AI)."""
    def sub(m):
        s = m.group(0).strip()
        if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}", s):
            return m.group(0)                                # a date, not a phone number
        digits = re.sub(r"\D", "", s)
        return mask(digits) if 7 <= len(digits) <= 15 else m.group(0)
    return PHONE_RE.sub(sub, text or "")


def _protect(data: bytes) -> bytes:
    """Encrypt for the current Windows user (DPAPI). Elsewhere (tests) the data is only encoded."""
    if os.name != "nt":
        return base64.b64encode(data)
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "Jako WhatsApp contacts", None, None, None, 0, ctypes.byref(out)):
        raise WhatsAppError("Windows couldn't encrypt the contacts.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        return base64.b64decode(data)
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise WhatsAppError("I couldn't unlock the saved contacts (they belong to another Windows user).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


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


def _write(contacts: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_protect(json.dumps(contacts, ensure_ascii=False).encode("utf-8")))


def load_contacts(path: Path = CONTACTS) -> dict[str, str]:
    old = OLD_CSV if path == CONTACTS else path.with_suffix(".csv")
    if old.exists():                                         # move an old plain-text list into the encrypted file
        with old.open(encoding="utf-8", newline="") as f:
            plain = {r["name"].strip(): r["phone"].strip() for r in csv.DictReader(f) if r.get("name") and r.get("phone")}
        merged = {**(json.loads(_unprotect(path.read_bytes())) if path.exists() else {}), **plain}
        _write(merged, path)
        old.unlink()
    if not path.exists():
        return {}
    return json.loads(_unprotect(path.read_bytes()).decode("utf-8"))


def save_contact(name: str, number: str, path: Path = CONTACTS) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise WhatsAppError("Which name should I save the number under?")
    phone = clean_number(number)
    contacts = {n: p for n, p in load_contacts(path).items() if _norm(n) != _norm(name)}
    contacts[name] = phone
    _write(contacts, path)
    return f"Saved {name}'s WhatsApp number ({mask(phone)}), encrypted on this laptop."


def forget_contact(name: str, path: Path = CONTACTS) -> str:
    contacts = load_contacts(path)
    hit, options = match_contact(name, list(contacts))
    if not hit:
        return f"Which one: {', '.join(options)}?" if options else f"I don't have {name} saved."
    del contacts[hit]
    _write(contacts, path)
    return f"Deleted {hit} from your WhatsApp contacts."


def list_contacts(path: Path = CONTACTS) -> str:
    c = load_contacts(path)
    if not c:
        return "No WhatsApp contacts saved yet. Say for example: save Mahir's WhatsApp number plus 49 170 1234567."
    return "WhatsApp contacts: " + ", ".join(f"{n} ({mask(p)})" for n, p in sorted(c.items(), key=lambda x: x[0].lower())) + "."


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


VOICE = ("voice call", "audio call", "sprachanruf", "audioanruf")
VIDEO = ("video call", "videoanruf")
CALL_MENU = ("call", "anrufen", "start call")


def _header_buttons(win) -> list:
    """Visible buttons in the open chat's top bar (right part, top strip of the window), never the left side tabs."""
    r = win.rectangle()
    top, left = r.top + max(140, int(r.height() * 0.16)), r.left + int(r.width() * 0.35)
    seen, out = set(), []
    for el in win.descendants(control_type="Button"):
        try:
            b = el.rectangle()
            if b.width() < 3 or b.height() < 3 or b.top > top or b.left < left:
                continue
            key = (b.left, b.top, el.window_text())
            if key not in seen:
                seen.add(key)
                out.append(el)
        except Exception:
            continue
    return out


def _named(buttons, names):
    for el in buttons:
        n = _norm(el.window_text())
        if any(n == x or n.startswith(x + " ") for x in names):
            return el
    return None


def call_click(video: bool) -> None:
    """Click the call button in the open chat's top bar."""
    from pywinauto import Desktop
    win = Desktop(backend="uia").window(title="WhatsApp", control_type="Window").wrapper_object()
    buttons = _header_buttons(win)
    btn = _named(buttons, VIDEO if video else VOICE)
    if btn is None:                                         # newer WhatsApp: one "Call" button that opens a small menu
        menu = _named(buttons, CALL_MENU)
        if menu is not None:
            menu.click_input()
            time.sleep(1.0)
            for ct in ("MenuItem", "Button", "ListItem"):
                for el in win.descendants(control_type=ct):
                    n = _norm(el.window_text())
                    if any(n == x or n.startswith(x) for x in (VIDEO if video else VOICE)):
                        btn = el
                        break
                if btn is not None:
                    break
    if btn is None:
        names = sorted({(b.window_text() or "")[:40] for b in buttons})
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "whatsapp-ui.txt").write_text("chat top-bar buttons: " + repr(names), encoding="utf-8")
        raise WhatsAppError("I opened the chat but couldn't find WhatsApp's call button, so no call was started.")
    if _foreground_title() != "WhatsApp":
        raise WhatsAppError("WhatsApp lost focus, so I didn't press anything.")
    btn.click_input()


def call(contact: str, video: bool = False) -> str:
    name, phone = find(contact)
    _open_chat(phone)
    call_click(video)
    return f"Calling {name} on WhatsApp ({'video' if video else 'voice'} call)."
