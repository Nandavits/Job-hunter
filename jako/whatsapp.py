"""WhatsApp on the Windows laptop: send a message, or start a voice or video call.

How it works (fast and reliable, no hunting for buttons):
- Jako keeps Nan's WhatsApp contacts (name, number) in data/whatsapp-contacts.csv on the laptop. They are added
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
import threading
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
    if _foreground_title() != "WhatsApp":                   # something else came to the front: bring WhatsApp back once
        _bring_whatsapp_forward()
        time.sleep(0.8)
    if _foreground_title() != "WhatsApp":                   # never type into another app
        raise WhatsAppError("WhatsApp lost focus, so I didn't press anything.")
    send_keys(keys)


# ---------------------------------------------------------------- actions
def send_message(contact: str, text: str) -> str:
    if not (text or "").strip():
        raise WhatsAppError("There's no message text to send.")
    name, phone = find(contact)
    if not _BUSY.acquire(blocking=False):
        raise WhatsAppError("I'm still busy with the last WhatsApp call or message. Give me a moment.")
    try:
        _open_chat(phone, text.strip())
        _press("{ENTER}")
    finally:
        _BUSY.release()
    return f"Sent to {name} on WhatsApp: “{text.strip()}”"


VOICE = ("voice call", "audio call", "sprachanruf", "audioanruf")
VIDEO = ("video call", "videoanruf")
CALL_MENU = ("call", "anrufen", "start call")

# Windows UI Automation ids
NAME, RECT, CONTROL_TYPE = 30005, 30001, 30003
BUTTON, WINDOW, MENU_ITEM = 50000, 50032, 50011
CHILDREN, DESCENDANTS = 2, 4
INVOKE = 10000

_BUSY = threading.Lock()                                    # one WhatsApp action at a time: retries can't pile up


def _named(buttons, names):
    """buttons: [(element, name, (left, top, right, bottom))]. The first whose name is one of `names`."""
    for b in buttons:
        n = _norm(b[1])
        if any(n == x or n.startswith(x + " ") for x in names):
            return b
    return None


def _header(buttons, win_rect):
    """Only the open chat's top bar: the top strip of the window, right of the chat list (never the left tabs)."""
    l, t, r, btm = win_rect
    top, left = t + max(140, int((btm - t) * 0.16)), l + int((r - l) * 0.35)
    return [b for b in buttons if b[2][2] - b[2][0] > 2 and b[2][3] - b[2][1] > 2 and b[2][1] <= top and b[2][0] >= left]


def _uia():
    from pywinauto.uia_defines import IUIA
    return IUIA()


def _whatsapp_window(ia):
    cond = ia.iuia.CreateAndCondition(ia.iuia.CreatePropertyCondition(NAME, "WhatsApp"),
                                      ia.iuia.CreatePropertyCondition(CONTROL_TYPE, WINDOW))
    win = ia.iuia.GetRootElement().FindFirst(CHILDREN, cond)
    if not win:
        raise WhatsAppError("I can't see the WhatsApp window.")
    return win


def _all_buttons(ia, root, control_type=BUTTON):
    """All buttons with their names and positions in ONE request to Windows (fast even on a busy laptop)."""
    req = ia.iuia.CreateCacheRequest()
    req.AddProperty(NAME)
    req.AddProperty(RECT)
    arr = root.FindAllBuildCache(DESCENDANTS, ia.iuia.CreatePropertyCondition(CONTROL_TYPE, control_type), req)
    out = []
    for i in range(arr.Length if arr else 0):
        e = arr.GetElement(i)
        r = e.CachedBoundingRectangle
        out.append((e, e.CachedName or "", (r.left, r.top, r.right, r.bottom)))
    return out


def _invoke(ia, element) -> None:
    """Press a button the way a screen reader does: works even when WhatsApp isn't the front window."""
    try:
        pattern = element.GetCurrentPattern(INVOKE).QueryInterface(ia.UIA_dll.IUIAutomationInvokePattern)
        pattern.Invoke()
    except Exception:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
        _bring_whatsapp_forward()
        UIAWrapper(UIAElementInfo(element)).click_input()


def call_click(video: bool) -> None:
    """Press the call button in the open chat's top bar."""
    ia = _uia()
    names = VIDEO if video else VOICE
    end = time.time() + 12
    while True:
        win = _whatsapp_window(ia)
        r = win.CurrentBoundingRectangle
        header = _header(_all_buttons(ia, win), (r.left, r.top, r.right, r.bottom))
        hit = _named(header, names)
        if hit is None:                                     # newer WhatsApp: one "Call" button that opens a small menu
            menu = _named(header, CALL_MENU)
            if menu is not None:
                _invoke(ia, menu[0])
                time.sleep(1.0)
                hit = _named(_all_buttons(ia, win, MENU_ITEM) + _all_buttons(ia, win), names)
        if hit is not None:
            _invoke(ia, hit[0])
            return
        if time.time() > end:                               # the chat may still be loading: look again for a few seconds
            break
        time.sleep(1.5)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "whatsapp-ui.txt").write_text("chat top-bar buttons: " + repr(sorted({b[1][:40] for b in header})),
                                                  encoding="utf-8")
    raise WhatsAppError("I opened the chat but couldn't find WhatsApp's call button, so no call was started.")


def call(contact: str, video: bool = False) -> str:
    name, phone = find(contact)
    if not _BUSY.acquire(blocking=False):
        raise WhatsAppError("I'm still busy with the last WhatsApp call or message. Give me a moment.")
    try:
        _open_chat(phone)
        call_click(video)
    finally:
        _BUSY.release()
    return f"Calling {name} on WhatsApp ({'video' if video else 'voice'} call)."
