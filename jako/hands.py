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
import fnmatch
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
    "whatsapp": ["WhatsApp", "whatsapp:"], "telegram": ["Telegram", "tg:"], "store": ["ms-windows-store:"],
    "snipping tool": ["Snipping Tool"], "photos": ["Photos"], "clock": ["Clock"], "mail": ["Outlook"],
}
_store_apps: dict = {"at": 0.0, "apps": []}


def _start_apps() -> list[dict]:
    """Every app in the Start menu, including Microsoft Store apps such as WhatsApp (cached 10 minutes)."""
    if time.time() - _store_apps["at"] < 600:
        return _store_apps["apps"]
    import json
    out = subprocess.run(["powershell", "-NoProfile", "-Command",  # nosec - fixed command, no user input
                          "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                         capture_output=True, text=True, timeout=30)
    try:
        apps = json.loads(out.stdout or "[]")
        apps = apps if isinstance(apps, list) else [apps]
    except ValueError:
        apps = []
    _store_apps.update(at=time.time(), apps=apps)
    return apps


def _store_app(name: str) -> dict | None:
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    if not want or sys.platform != "win32":
        return None
    best = None
    for app in _start_apps():
        have = re.sub(r"[^a-z0-9]", "", str(app.get("Name", "")).lower())
        if have == want:
            return app
        if want in have and (best is None or len(have) < len(re.sub(r"[^a-z0-9]", "", best["Name"].lower()))):
            best = app
    return best


WEBSITES = {  # spoken name -> address (opened in the default browser)
    "github": "https://github.com", "linkedin": "https://www.linkedin.com", "gmail": "https://mail.google.com",
    "google": "https://www.google.com", "youtube": "https://www.youtube.com", "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "google calendar": "https://calendar.google.com", "calendar": "https://calendar.google.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com", "stepstone": "https://www.stepstone.de",
    "xing": "https://www.xing.com", "indeed": "https://de.indeed.com", "glassdoor": "https://www.glassdoor.de",
    "whatsapp web": "https://web.whatsapp.com", "outlook web": "https://outlook.live.com", "netflix": "https://www.netflix.com",
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
    key = name.lower().strip().removesuffix(" app").removesuffix(" website").removesuffix(".com").strip()
    if key in WEBSITES and key not in APPS:
        return open_url(WEBSITES[key])
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
        app = _store_app(target)
        if app and re.fullmatch(r"[\w.!{}\\:~ -]{1,200}", str(app.get("AppID", ""))):
            _start("shell:AppsFolder\\" + app["AppID"])
            return f"Opened {app['Name']}."
    if key in WEBSITES:
        return open_url(WEBSITES[key])
    raise Refused(f"I couldn't find an app called '{name}'. If it's a website, ask me to open its web address.")


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


SKIP_DIRS = {"appdata", "node_modules", "__pycache__", "site-packages", ".venv", "venv", ".venv-hands",
             "$recycle.bin", "windows", "program files", "program files (x86)", "programdata"}


def find_files(name: str, folder: str = "") -> str:
    root = safe_path(folder or str(HOME))
    pattern = name.strip()
    if not pattern:
        raise Refused("Tell me part of the file name.")
    glob = (pattern if any(c in pattern for c in "*?") else f"*{pattern}*").lower()
    hits, deadline, timed_out = [], time.time() + 8, False
    for dirpath, dirnames, filenames in os.walk(root):
        if time.time() > deadline:                      # checked per folder, so a search with no hits stops too
            timed_out = True
            break
        # skip hidden, system and huge program folders (AppData, node_modules, .git ...)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS]
        for f in dirnames + filenames:
            if fnmatch.fnmatch(f.lower(), glob):
                p = Path(dirpath) / f
                try:
                    safe_path(str(p))
                except Refused:
                    continue
                hits.append(p)
        if len(hits) >= 25:
            break
    if not hits:
        more = " (I stopped after 8 seconds; tell me a folder to search more closely.)" if timed_out else ""
        return f"No files matching '{pattern}' in {root}.{more}"
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


def top_processes(count: int = 8) -> str:
    import psutil
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["name"] or "?", p.info["memory_info"].rss, p.cpu_percent(None), p))
        except (psutil.Error, AttributeError):
            continue
    time.sleep(0.6)
    rows = []
    for name, rss, _, p in procs:
        try:
            rows.append((name, rss, p.cpu_percent(None)))
        except psutil.Error:
            continue
    merged: dict = {}
    rows = [r for r in rows if r[0] not in ("System Idle Process", "System", "Idle", "Registry", "Memory Compression")]
    for name, rss, cpu in rows:                          # one line per program (Chrome has many processes)
        m = merged.setdefault(name, [0, 0.0, 0])
        m[0] += rss
        m[1] += cpu
        m[2] += 1
    top = sorted(merged.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:max(3, min(int(count), 20))]
    cores = psutil.cpu_count() or 1
    return "Busiest programs right now:\n" + "\n".join(
        f"{n}: CPU {c / cores:.0f}%, memory {r / 1e6:.0f} MB" + (f" ({k} processes)" if k > 1 else "")
        for n, (r, c, k) in top)


def _folder_size(path: Path, deadline: float) -> int:
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        if time.time() > deadline:
            break
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _recycle_bin_bytes() -> int:
    if sys.platform != "win32":
        return 0

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))  # type: ignore[attr-defined]
    return int(info.i64Size)


def _temp_dir() -> Path:
    return Path(os.environ.get("TEMP") or HOME / "AppData" / "Local" / "Temp")


def _old_temp_files(days: int) -> list[Path]:
    cutoff, out = time.time() - days * 86400, []
    for root, dirs, files in os.walk(_temp_dir(), onerror=lambda e: None):
        for f in files:
            p = Path(root) / f
            try:
                if p.stat().st_mtime < cutoff:
                    out.append(p)
            except OSError:
                pass
    return out


def disk_report() -> str:
    import psutil
    lines = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            lines.append(f"Drive {part.device}: {u.free / 1e9:.0f} GB free of {u.total / 1e9:.0f} GB ({u.percent:.0f}% used)")
        except (PermissionError, OSError):
            continue
    deadline = time.time() + 20
    for name in ["Downloads", "Desktop", "Documents", "Videos", "Pictures"]:
        folder = HOME / name
        if folder.exists():
            lines.append(f"{name}: {_folder_size(folder, deadline) / 1e9:.1f} GB")
    temp = _old_temp_files(7)
    temp_mb = sum(p.stat().st_size for p in temp if p.exists()) / 1e6
    lines.append(f"Temporary files older than 7 days: {len(temp)} files, {temp_mb:.0f} MB (safe to clean)")
    lines.append(f"Recycle Bin: {_recycle_bin_bytes() / 1e6:.0f} MB")
    return "\n".join(lines)


def old_files(folder: str = "Downloads", months: int = 6, min_mb: int = 20, limit: int = 25) -> str:
    root = safe_path(folder or "Downloads")
    cutoff = time.time() - max(1, int(months)) * 30 * 86400
    hits, deadline = [], time.time() + 20
    for dirpath, dirs, files in os.walk(root, onerror=lambda e: None):
        if time.time() > deadline:
            break
        dirs[:] = [d for d in dirs if d.lower() not in ("appdata", ".git", "node_modules", ".venv", "venv")]
        for f in files:
            p = Path(dirpath) / f
            try:
                st = p.stat()
                last = max(st.st_mtime, st.st_atime)
                if st.st_size >= min_mb * 1e6 and last < cutoff:
                    safe_path(str(p))
                    hits.append((st.st_size, last, p))
            except (OSError, Refused):
                continue
    if not hits:
        return f"No files over {min_mb} MB unused for {months} months in {root}."
    hits.sort(reverse=True)
    total = sum(h[0] for h in hits)
    return (f"{len(hits)} files over {min_mb} MB not used for {months}+ months in {root} ({total / 1e9:.1f} GB):\n"
            + "\n".join(f"{p}  {size / 1e6:.0f} MB, last used {time.strftime('%d.%m.%Y', time.localtime(last))}"
                         for size, last, p in hits[:max(1, min(int(limit), 50))]))


PERF_FILE = ROOT / "data" / "perf.csv"


def sample_performance() -> None:
    """One line every 10 minutes for Hulk's day-to-day report (data/perf.csv, last 30 days kept)."""
    import psutil
    bat = psutil.sensors_battery()
    disk = psutil.disk_usage(str(HOME.anchor or HOME))
    top = ""
    try:
        procs = sorted(psutil.process_iter(["name", "memory_info"]), key=lambda p: -(p.info["memory_info"].rss
                                                                                     if p.info["memory_info"] else 0))
        top = procs[0].info["name"] if procs else ""
    except psutil.Error:
        pass
    row = [time.strftime("%Y-%m-%d %H:%M"), f"{psutil.cpu_percent(interval=1):.0f}",
           f"{psutil.virtual_memory().percent:.0f}", f"{disk.free / 1e9:.1f}",
           f"{bat.percent:.0f}" if bat else "", ("1" if bat and bat.power_plugged else "0") if bat else "", top]
    PERF_FILE.parent.mkdir(parents=True, exist_ok=True)
    new = not PERF_FILE.exists()
    with open(PERF_FILE, "a", encoding="utf-8") as f:
        if new:
            f.write("time,cpu,ram,disk_free_gb,battery,plugged,top_memory_app\n")
        f.write(",".join(row) + "\n")
    lines = PERF_FILE.read_text(encoding="utf-8").splitlines()
    if len(lines) > 30 * 144 + 1:
        PERF_FILE.write_text("\n".join([lines[0]] + lines[-30 * 144:]) + "\n", encoding="utf-8")


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


def _do_clean_temp(days: int = 7) -> str:
    files, freed, failed = _old_temp_files(int(days)), 0, 0
    for p in files:
        try:
            size = p.stat().st_size
            p.unlink()
            freed += size
        except OSError:
            failed += 1                                   # in use by a running program: skip
    return f"Removed {len(files) - failed} temporary files and freed {freed / 1e6:.0f} MB." + (
        f" {failed} were in use and stayed." if failed else "")


def _do_empty_recycle_bin() -> str:
    if sys.platform != "win32":
        raise Refused("This only works on Windows.")
    size = _recycle_bin_bytes()
    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)  # type: ignore[attr-defined]
    return f"Emptied the Recycle Bin and freed {size / 1e6:.0f} MB."


def _do_open_program(path: str) -> str:
    p = safe_path(path)
    _start(str(p))
    return f"Started {p.name}."


RISKY = {"run_command": _do_run_command, "write_file": _do_write_file, "move": _do_move,
         "delete": _do_delete, "close_app": _do_close_app, "open_program": _do_open_program,
         "clean_temp": _do_clean_temp, "empty_recycle_bin": _do_empty_recycle_bin}
SAFE = {"open_app": open_app, "open_url": open_url, "open_path": open_path, "find_files": find_files,
        "list_folder": list_folder, "read_file": read_file, "screenshot": screenshot, "volume": volume,
        "system_info": system_info, "lock_screen": lock_screen, "top_processes": top_processes,
        "disk_report": disk_report, "old_files": old_files}


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
        "clean_temp": _describe_temp(int(args.get("days", 7) or 7)),
        "empty_recycle_bin": f"permanently empty the Recycle Bin ({_recycle_bin_bytes() / 1e6:.0f} MB, can't be undone)",
    }.get(action, f"{action} {a}")


def _describe_temp(days: int) -> str:
    files = _old_temp_files(days)
    mb = sum(p.stat().st_size for p in files if p.exists()) / 1e6
    return f"permanently remove {len(files)} temporary files older than {days} days ({mb:.0f} MB)"


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


BROWSERS = [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%LocalAppData%\Google\Chrome\Application\chrome.exe"]
PAUSE_FLAG = ROOT / "data" / "jako-stopped.flag"


def jako_window_open() -> bool:
    import psutil
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() in ("msedge.exe", "chrome.exe") and \
                    any("jako-window" in (a or "") for a in (p.info["cmdline"] or [])):
                return True
        except psutil.Error:
            continue
    return False


def window_url(base: str) -> str:
    """The window must use localhost: the microphone permission is saved for localhost, not 127.0.0.1."""
    return base.replace("://127.0.0.1", "://localhost").rstrip("/") + "/"


def open_jako_window(base: str, minimized: bool = True) -> bool:
    """Open Jako's own window (fixed browser command, our own address)."""
    for raw in BROWSERS:
        exe = os.path.expandvars(raw)
        if os.path.isfile(exe):
            args = [exe, f"--app={window_url(base)}", f"--user-data-dir={ROOT / 'data' / 'jako-window'}",
                    "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check",
                    "--start-minimized" if minimized else "--start-maximized"]
            subprocess.Popen(args, cwd=str(ROOT))  # nosec B603 - fixed program and arguments
            return True
    return False


def watch_window(base: str, check=None, opener=None, healthy=None) -> str:
    """If Jako runs but its window was closed, open it again (minimised), unless Jako was stopped on purpose."""
    if PAUSE_FLAG.exists():
        return "stopped"
    if (check or jako_window_open)():
        return "open"
    if not (healthy or _jako_healthy)(base):
        return "jako not running"
    return "reopened" if (opener or open_jako_window)(base) else "no browser"


def _jako_healthy(base: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=3):  # nosec B310 - local Jako address
            return True
    except Exception:
        return False


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

    def sampler():
        while True:
            try:
                sample_performance()
            except Exception as exc:                    # never stop the link because of a sample
                print(time.strftime("%H:%M:%S"), f"performance sample failed: {exc}", flush=True)
            time.sleep(600)
    def watchdog():
        time.sleep(90)                                   # give Jako time to start after sign-in
        while True:
            try:
                if watch_window(base) == "reopened":
                    print(time.strftime("%H:%M:%S"), "Jako window was closed - opened it again (minimised)", flush=True)
            except Exception as exc:
                print(time.strftime("%H:%M:%S"), f"window check failed: {exc}", flush=True)
            time.sleep(60)
    import threading
    threading.Thread(target=sampler, daemon=True).start()
    if sys.platform == "win32":
        threading.Thread(target=watchdog, daemon=True).start()
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
