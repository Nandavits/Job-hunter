"""The _UPLOAD folder: the CV and cover letter for the job you are applying to right now.

On sites you fill in by hand (Workday, Lever, EA, SuccessFactors ...) you no longer dig through
output/2026-11-19_Company_Title/: every time you press Apply, Job Hunter puts that job's files in
job-hunter/_UPLOAD with short names. The file picker remembers the folder, so after the first
time it opens there straight away.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .config import ROOT

UPLOAD_DIR = ROOT / "_UPLOAD"
OURS = {".pdf", ".docx", ".txt"}


def _person(profile: dict) -> str:
    me = (profile or {}).get("personal", {})
    name = "_".join(p for p in (me.get("first_name", "").split()[:1] + me.get("last_name", "").split()[-1:]) if p)
    return name or "Me"


def fill_upload_folder(job: dict, cv: Path, letter: Path | None, answers: str, profile: dict,
                       folder: Path = UPLOAD_DIR) -> list[Path]:
    """Replace what is in _UPLOAD with this job's CV, cover letter and answers. Returns the files."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():                     # only files Job Hunter itself put here
        if old.is_file() and old.suffix.lower() in OURS:
            old.unlink()
    me, made = _person(profile), []
    for src, stem in ((cv, f"CV_{me}"), (letter, f"Cover_Letter_{me}")):
        if src and Path(src).is_file():
            for twin in {Path(src), Path(src).with_suffix(".pdf"), Path(src).with_suffix(".docx")}:
                if twin.is_file():                   # PDF and Word, whichever exist
                    made.append(Path(shutil.copy2(twin, folder / f"{stem}{twin.suffix.lower()}")))
    head = f"{job.get('company', '')} - {job.get('title', '')}\n{job.get('apply_url') or job.get('url') or ''}\n\n"
    (folder / "answers.txt").write_text(head + answers, encoding="utf-8")
    made.append(folder / "answers.txt")
    return made


def open_folder(folder: Path = UPLOAD_DIR) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(folder))                    # nosec B606 - opens File Explorer on our own folder


def chrome_path() -> str:
    """Google Chrome on Windows (where you are signed in and upload files), if installed."""
    if not sys.platform.startswith("win"):
        return ""
    for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        exe = Path(base or ".") / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and exe.is_file():
            return str(exe)
    return ""


def open_in_browser(url: str) -> None:
    """Open a page in Google Chrome when it is installed, otherwise in the default browser."""
    import subprocess
    import webbrowser
    exe = chrome_path()
    if exe:
        subprocess.Popen([exe, url])                 # nosec B603 - fixed program, our own URL
    else:
        webbrowser.open(url)
