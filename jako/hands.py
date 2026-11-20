"""Jako's hands: a small helper that runs directly on Windows (not in Docker) so Iron Man can use the laptop.

Safety rules, enforced here and not by the AI:
- Only takes jobs from Jako on this computer, using the secret JAKO_HANDS_TOKEN.
- Safe actions run at once: open an app, website or document, find/list/read files, screenshot, volume,
  system info, lock the screen.
- Everything that changes something (run a command, write/move/delete a file, close an app, open a program file)
  only creates a *pending* action with a code. It runs after you say "yes" to Jako; the AI itself cannot confirm.
- Files: only inside your user folder, never secret folders (.ssh, browser profiles, password files, .env ...).
- Deleting moves to the Recycle Bin, so it can be undone.

It talks to Jako by asking http://127.0.0.1:8765 for jobs (Docker can't connect into the laptop, so the
helper calls out instead; nothing on the laptop listens on the network).
Start: start-jako.bat or jako-laptop-link.bat does it for you (python -m jako.hands).
"""
from __future__ import annotations

import base64
import ctypes
import io
import os
import platform
import re
import secrets
import shutil
import subprocess  # nosec B404 - commands only after the user confirms
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("JAKO_HANDS_HOME") or Path.home()).resolve()
PENDING_SECONDS = 300

BLOCKED_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "credentials", "keychain",
                 "login data", "cookies", "user data", "protect", "crypto", "vault"}
BLOCKED_NAMES = re.compile(r"(^\.env|password|passwort|secret|token|\.kdbx$|\.pem$|\.key$|\.pfx$|id_rsa|id_ed25519)",
                           re.I)
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".py", ".sql", ".log", ".html", ".xml", ".ini",
              ".cfg", ".toml", ".js", ".ts", ".css", ".bat", ".ps1", ".tex", ".rst"}
PROGRAM_TYPES = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".lnk", ".scr", ".com", ".jar", ".py",
                 ".reg", ".hta", ".wsf", ".appref-ms"}

APPS = {  # spoken name -> things to try: "x:" = Windows link, "x.exe" = program, else a Start-menu shortcut name
    "notepad": ["notepad.exe"], "calculator": ["calc.exe"], "calc": ["calc.exe"], "paint": ["mspaint.exe"],
    "chrome": ["Google Chrome", "chrome.exe"], "google chrome": ["Google Chrome", "chrome.exe"],
    "edge": ["Microsoft Edge", "msedge.exe"], "browser": ["Google Chrome", "Microsoft Edge", "msedge.exe"],
    "explorer": ["explorer.exe"], "file explorer": ["explorer.exe"], "files": ["explorer.exe"],
    "vs code": ["Visual Studio Code"], "vscode": ["Visual Studio Code"], "visual studio code": ["Visual Studio Code"],
    "terminal": ["Terminal", "wt.exe"], "word": ["Word", "winword.exe"], "excel": ["Excel", "excel.exe"],
    "powerpoint": ["PowerPoint", "powerpnt.exe"], "outlook": ["Outlook", "outlook.exe"], "teams": ["ms-teams:"],
    "spotify": ["Spotify", "spotify:"], "settings": ["ms-settings:"], "task manager": ["taskmgr.exe"],
    "docker": ["Docker Desktop"], "pycharm": ["PyCharm"], "camera": ["microsoft.windows.camera:"],
}


class Refused(Exception):
    pass


# ---------- paths ----------
def safe_path(raw: str, must_exist: bool = True) -> Path:
    text = os.path.expandvars(os.path.expanduser((raw or "").strip().strip('"')))
    if not text:
        raise Refused("No path given.")
    p = Path(text)
    if not p.is_absolute():
        p = HOME / p
    p = p.resolve()
    if p != HOME and HOME not in p.parents:
        raise Refused(f"I only work inside your user folder ({HOME}).")
    parts = {x.lower() for x in p.relative_to(HOME).parts}
    if parts & BLOCKED_PARTS or any(BLOCKED_NAMES.search(x) for x in p.relative_to(HOME).parts):
        raise Refused("That location may hold passwords or keys, so I keep out of it.")
    if must_exist and not p.exists():
        raise Refused(f"Not found: {p}")
    return p


# ---------- safe actions ----------
def _start(target: str) -> None:
    if sys.platform != "win32":
        raise Refused("This only works on Windows.")
    os.startfile(target)  # type: ignore[attr-defined]  # nosec B606 - fixed app list or Start-menu shortcut


def _start_menu_shortcut(name: str) -> Path | None:
    roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ.get("APPDATA", str(HOME))) / r"Microsoft\Windows\Start Menu\Programs"]
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    best = None
    for root in roots:
        for lnk in root.rglob("*.lnk") if root.exists() else []:
            stem = re.sub(r"[^a-z0-9]", "", lnk.stem.lower())
            if stem == want:
                return lnk
            if want and want in stem and (best is None or len(lnk.stem) < len(best.stem)):
                best = lnk
    return best


def open_app(name: str) -> str:
    key = name.lower().strip()
    for target in APPS.get(key, []) + [key]:
        if target.endswith(":") or target.endswith(".exe"):
            if target == key:
                continue
            try:
                _start(target)
                return f"Opened {name}."
            except OSError:
                continue
        shortcut = _start_menu_shortcut(target)
        if shortcut:
            _start(str(shortcut))
            return f"Opened {shortcut.stem}."
    raise Refused(f"I couldn't find an app called '{name}' in the Start menu.")


def open_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://[^\s\"'<>]+$", url):
        raise Refused("I only open normal web addresses (http or https).")
    _start(url)
    return f"Opened {url} in your browser."


def open_path(path: str) -> str:
    p = safe_path(path)
    _start(str(p))
    return f"Opened {p}."


def find_files(name: str, folder: str = "") -> str:
    root = safe_path(folder or str(HOME))
    pattern = name.strip()
    if not pattern:
        raise Refused("Tell me part of the file name.")
    glob = pattern if any(c in pattern for c in "*?") else f"*{pattern}*"
    hits, deadline = [], time.time() + 8
    for p in root.rglob(glob):
        if time.time() > deadline or len(hits) >= 25:
            break
        try:
            safe_path(str(p))
        except Refused:
            continue
        if "appdata" in {x.lower() for x in p.parts}:
            continue
        hits.append(p)
    if not hits:
        return f"No files matching '{pattern}' in {root}."
    hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return "\n".join(f"{p}  ({time.strftime('%d.%m.%Y', time.localtime(p.stat().st_mtime))})" for p in hits)


def list_folder(path: str = "") -> str:
    p = safe_path(path or str(HOME))
    if not p.is_dir():
        raise Refused(f"{p} is not a folder.")
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:60]
    return f"{p}:\n" + "\n".join(("[folder] " if i.is_dir() else "") + i.name for i in items)


def read_file(path: str) -> str:
    p = safe_path(path)
    if p.suffix.lower() not in TEXT_TYPES:
        raise Refused(f"I can only read text files, not {p.suffix or 'this type'}.")
    if p.stat().st_size > 300_000:
        raise Refused("That file is too big to read aloud (over 300 KB).")
    return p.read_text(encoding="utf-8", errors="replace")[:20000]


def screenshot() -> dict:
    from PIL import ImageGrab
    img = ImageGrab.grab()
    folder = HOME / "Pictures" / "Jako"
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / time.strftime("screen_%Y%m%d_%H%M%S.png")
    img.save(saved)
    img.thumbnail((1400, 1400))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {"text": f"Screenshot saved to {saved}.", "image_b64": base64.b64encode(buf.getvalue()).decode()}


def volume(direction: str, steps: int = 5) -> str:
    keys = {"up": 0xAF, "down": 0xAE, "mute": 0xAD, "unmute": 0xAD}
    if direction not in keys:
        raise Refused("Volume can go up, down or mute.")
    if sys.platform != "win32":
        raise Refused("This only works on Windows.")
    n = 1 if direction in ("mute", "unmute") else max(1, min(int(steps), 25))
    for _ in range(n):
        ctypes.windll.user32.keybd_event(keys[direction], 0, 0, 0)  # type: ignore[attr-defined]
        ctypes.windll.user32.keybd_event(keys[direction], 0, 2, 0)  # type: ignore[attr-defined]
    return f"Volume {direction}."


def system_info() -> str:
    import psutil
    lines = [f"Computer: {platform.node()} ({platform.system()} {platform.release()})"]
    bat = psutil.sensors_battery()
    if bat:
        lines.append(f"Battery: {bat.percent:.0f}% {'charging' if bat.power_plugged else 'on battery'}")
    disk = psutil.disk_usage(str(HOME.anchor or HOME))
    lines.append(f"Disk: {disk.free / 1e9:.0f} GB free of {disk.total / 1e9:.0f} GB")
    mem = psutil.virtual_memory()
    lines.append(f"Memory: {mem.percent:.0f}% used, CPU: {psutil.cpu_percent(interval=0.5):.0f}%")
    lines.append(f"Up since: {time.strftime('%d.%m %H:%M', time.localtime(psutil.boot_time()))}")
    return "\n".join(lines)


def lock_screen() -> str:
    if sys.platform != "win32":
        raise Refused("This only works on Windows.")
    ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
    return "Screen locked."


# ---------- actions that need your "yes" ----------
def _do_run_command(command: str) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,  # nosec
                         timeout=90, cwd=str(HOME))
    text = (out.stdout + ("\n" + out.stderr if out.stderr.strip() else "")).strip()
    return f"Exit code {out.returncode}.\n{text[:6000]}" if text else f"Done (exit code {out.returncode})."


def _do_write_file(path: str, text: str) -> str:
    p = safe_path(path, must_exist=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return f"Saved {p}."


def _do_move(source: str, target: str) -> str:
    src, dst = safe_path(source), safe_path(target, must_exist=False)
    shutil.move(str(src), str(dst))
    return f"Moved {src.name} to {dst}."


def _recycle(p: Path) -> None:
    if sys.platform != "win32":
        raise Refused("This only works on Windows.")

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    op = SHFILEOPSTRUCTW(None, 3, str(p) + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, 0, None, None)  # FO_DELETE, undo
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0:  # type: ignore[attr-defined]
        raise Refused(f"Windows could not move {p.name} to the Recycle Bin.")


def _do_delete(path: str) -> str:
    p = safe_path(path)
    if p == HOME:
        raise Refused("I won't delete your whole user folder.")
    _recycle(p)
    return f"Moved {p.name} to the Recycle Bin. You can restore it from there."


def _do_close_app(name: str) -> str:
    exe = name.strip().lower().removesuffix(".exe")
    if not re.fullmatch(r"[\w .-]{1,60}", exe):
        raise Refused("That doesn't look like an app name.")
    out = subprocess.run(["taskkill", "/IM", f"{exe}.exe"], capture_output=True, text=True, timeout=20)  # nosec
    return out.stdout.strip() or out.stderr.strip() or f"Closed {exe}."


def _do_open_program(path: str) -> str:
    p = safe_path(path)
    _start(str(p))
    return f"Started {p.name}."


RISKY = {"run_command": _do_run_command, "write_file": _do_write_file, "move": _do_move,
         "delete": _do_delete, "close_app": _do_close_app, "open_program": _do_open_program}
SAFE = {"open_app": open_app, "open_url": open_url, "open_path": open_path, "find_files": find_files,
        "list_folder": list_folder, "read_file": read_file, "screenshot": screenshot, "volume": volume,
        "system_info": system_info, "lock_screen": lock_screen}


def describe(action: str, args: dict) -> str:
    a = {k: str(v)[:300] for k, v in args.items()}
    return {
        "run_command": f"run this command: {a.get('command', '')}",
        "write_file": f"write {len(args.get('text', ''))} characters to {a.get('path', '')}"
                      + (" (it already exists and will be overwritten)" if _exists(a.get("path")) else ""),
        "move": f"move {a.get('source', '')} to {a.get('target', '')}",
        "delete": f"move {a.get('path', '')} to the Recycle Bin",
        "close_app": f"close {a.get('name', '')}",
        "open_program": f"start the program {a.get('path', '')}",
    }.get(action, f"{action} {a}")


def _exists(path) -> bool:
    try:
        return safe_path(path or "", must_exist=False).exists()
    except Refused:
        return False


@dataclass
class Hands:
    pending: dict = field(default_factory=dict)        # code -> (action, args, summary, created)

    def do(self, action: str, args: dict) -> dict:
        args = dict(args or {})
        try:
            if action == "open_path" and Path(args.get("path", "")).suffix.lower() in PROGRAM_TYPES:
                action = "open_program"
            if action in SAFE:
                result = SAFE[action](**args)
                return result if isinstance(result, dict) else {"text": result}
            if action in RISKY:
                if action in ("write_file", "move", "delete", "open_program"):   # check paths before asking
                    for key in ("path", "source"):
                        if key in args:
                            safe_path(args[key], must_exist=action != "write_file")
                    if "target" in args:
                        safe_path(args["target"], must_exist=False)
                self._expire()
                code = secrets.token_hex(4)
                summary = describe(action, args)
                self.pending[code] = (action, args, summary, time.time())
                return {"pending": code, "text": f"Needs your OK: {summary}"}
            return {"error": f"Unknown action {action}."}
        except Refused as exc:
            return {"error": str(exc)}
        except TypeError as exc:
            return {"error": f"Wrong details for {action}: {exc}"}
        except Exception as exc:                        # report, don't crash the helper
            return {"error": f"{action} failed: {exc}"}

    def confirm(self, code: str) -> dict:
        self._expire()
        item = self.pending.pop(code, None)
        if not item:
            return {"error": "That request expired. Please ask again."}
        action, args, _, _ = item
        try:
            return {"text": RISKY[action](**args)}
        except Refused as exc:
            return {"error": str(exc)}
        except subprocess.TimeoutExpired:
            return {"error": "The command took longer than 90 seconds and was stopped."}
        except Exception as exc:
            return {"error": f"{action} failed: {exc}"}

    def cancel(self, code: str) -> dict:
        return {"text": "Cancelled."} if self.pending.pop(code, None) else {"text": "Nothing to cancel."}

    def _expire(self) -> None:
        now = time.time()
        for code in [c for c, v in self.pending.items() if now - v[3] > PENDING_SECONDS]:
            self.pending.pop(code, None)


def read_token() -> str:
    token = os.environ.get("JAKO_HANDS_TOKEN", "")
    env = ROOT / ".env"
    if not token and env.is_file():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("JAKO_HANDS_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
    return token


def create_app(token: str, hands: Hands | None = None):
    from fastapi import Body, FastAPI, Header, HTTPException

    if len(token) < 16:
        raise SystemExit("JAKO_HANDS_TOKEN is missing or too short in .env. start-jako.bat creates one.")
    hands = hands or Hands()
    app = FastAPI(title="Jako hands", docs_url=None, redoc_url=None, openapi_url=None)

    def check(x_jako_token: str | None):
        if not x_jako_token or not secrets.compare_digest(x_jako_token, token):
            raise HTTPException(status_code=401, detail="wrong token")

    @app.post("/do")
    def do(body: dict = Body(...), x_jako_token: str | None = Header(default=None)):
        check(x_jako_token)
        return hands.do(str(body.get("action", "")), body.get("args") or {})

    @app.post("/confirm")
    def confirm(body: dict = Body(...), x_jako_token: str | None = Header(default=None)):
        check(x_jako_token)
        return hands.confirm(str(body.get("code", "")))

    @app.post("/cancel")
    def cancel(body: dict = Body(...), x_jako_token: str | None = Header(default=None)):
        check(x_jako_token)
        return hands.cancel(str(body.get("code", "")))

    @app.get("/status")
    def status(x_jako_token: str | None = Header(default=None)):
        check(x_jako_token)
        hands._expire()
        return {"ok": True, "computer": platform.node(), "pending": len(hands.pending)}

    return app


def serve_once(hands: Hands, base: str, token: str, computer: str, opener=None) -> bool:
    """Ask Jako for one job, do it, send the result back. Returns True if there was a job."""
    import json
    import urllib.parse
    import urllib.request
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(f"{base}/api/hands/poll?computer={urllib.parse.quote(computer)}",
                                 headers={"X-Jako-Token": token})
    with opener(req, timeout=40) as r:  # nosec B310 - fixed local Jako address
        job = json.loads(r.read() or b"{}")
    if not job.get("id"):
        return False
    kind = job.get("kind")
    if kind == "do":
        result = hands.do(str(job.get("action", "")), job.get("args") or {})
    elif kind == "confirm":
        result = hands.confirm(str(job.get("code", "")))
    elif kind == "cancel":
        result = hands.cancel(str(job.get("code", "")))
    elif kind == "status":
        hands._expire()
        result = {"ok": True, "computer": computer, "pending": len(hands.pending)}
    else:
        result = {"error": f"Unknown job {kind}"}
    body = json.dumps({"id": job["id"], "result": result}).encode()
    post = urllib.request.Request(f"{base}/api/hands/result", data=body, method="POST",
                                  headers={"X-Jako-Token": token, "Content-Type": "application/json"})
    with opener(post, timeout=30):  # nosec B310
        pass
    return True


def main() -> None:
    if sys.stdout is None or sys.stderr is None:      # started hidden (pythonw): no console to write to
        (ROOT / "logs").mkdir(exist_ok=True)
        log = open(ROOT / "logs" / "jako-hands-run.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    token = read_token()
    if len(token) < 16:
        raise SystemExit("JAKO_HANDS_TOKEN is missing or too short in .env. start-jako.bat creates one.")
    base = os.environ.get("JAKO_URL", "http://127.0.0.1:8765").rstrip("/")
    hands, computer, quiet = Hands(), platform.node(), 0
    print(time.strftime("%H:%M:%S"), f"Jako laptop link started on {computer}, talking to {base}", flush=True)
    while True:
        try:
            serve_once(hands, base, token, computer)
            quiet = 0
        except Exception as exc:                        # Jako not up yet, or restarting: try again soon
            if quiet == 0:
                print(time.strftime("%H:%M:%S"), f"waiting for Jako: {exc}", flush=True)
            quiet += 1
            time.sleep(5)


if __name__ == "__main__":
    main()
