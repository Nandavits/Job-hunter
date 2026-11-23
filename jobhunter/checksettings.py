"""Check profile.yaml / config.yaml for typing mistakes and missing parts, in plain words.

    python -m jobhunter check-settings                 # both files in the config folder
    python -m jobhunter check-settings path\\to\\file.yaml   # any copy, e.g. a friend's file

Used by check-settings.bat (drag a file onto it). Reads only; never changes a file.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

PROFILE_NEEDS = {"personal": ["first_name", "last_name", "email"]}


def _numbered(lines: list[str], first: int, last: int, mark: int) -> list[str]:
    out = []
    for i in range(max(first, 0), min(last, len(lines))):
        arrow = ">>" if i == mark else "  "
        out.append(f"   {arrow} line {i + 1:>3} | {lines[i].replace(chr(9), '[TAB]')}")
    return out


def _hint(line: str) -> str:
    stripped = line.strip()
    if "\t" in line:
        return "This line uses the Tab key. Replace it with spaces (2 spaces per level)."
    if stripped and not stripped.startswith(("-", "#")) and (":" not in stripped or
                                                              re.match(r"https?://", stripped)):
        return ("This line has no label and colon. It should look like  label: value  "
                "(for example  github: https://github.com/yourname  or  github: \"\").")
    if stripped.count(": ") >= 2 or re.search(r":\s.*:\s", stripped):
        return ("The value contains a colon. Put the whole value in double quotes, for example\n"
                "         text: \"Tools used: SQL, Python\"")
    if re.match(r"^\s*-?\s*\w[\w ]*:\S", line):
        return "There must be a space after the colon:  label: value"
    return ("Check the spaces at the start of this line and the line above: they must line up exactly "
            "like the lines around them in the example file.")


def check_text(text: str, name: str = "profile.yaml") -> tuple[bool, list[str]]:
    """Return (ok, report lines)."""
    lines = text.splitlines()
    report: list[str] = []
    tabs = [i for i, l in enumerate(lines) if "\t" in l[: len(l) - len(l.lstrip())]]
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "context_mark", None) or getattr(exc, "problem_mark", None)
        report.append(f"PROBLEM: {name} has a typing mistake, so Job Hunter cannot read it.")
        if mark is not None:
            bad = mark.line
            # the scanner often points one line late; show the neighbourhood and blame the likely line
            likely = bad
            for j in (bad, bad - 1):
                if 0 <= j < len(lines) and (_hint(lines[j]).startswith(("This line", "The value", "There must"))):
                    likely = j
                    break
            report.append(f"Look at line {likely + 1}:")
            report += _numbered(lines, likely - 2, likely + 3, likely)
            report.append("   What to do: " + _hint(lines[likely] if likely < len(lines) else ""))
        for i in tabs[:5]:
            report.append(f"   Line {i + 1} starts with a Tab: use spaces instead.")
        report.append("Fix it in Notepad, save (Ctrl+S) and check again. Only the first mistake is shown;"
                      " after fixing it, check again for the next one.")
        return False, report

    if not isinstance(data, dict):
        return False, [f"PROBLEM: {name} does not have the expected  label: value  layout."]

    problems: list[str] = []
    is_profile = "personal" in data or "experience" in data or name.startswith("profile")
    if is_profile:
        personal = data.get("personal") or {}
        for key in PROFILE_NEEDS["personal"]:
            if not str(personal.get(key) or "").strip():
                problems.append(f"personal > {key} is empty.")
        if str(personal.get("first_name", "")).strip().lower() == "jane":
            problems.append("personal still has the example name (Jane Doe): replace it with your own details.")
        exp = data.get("experience")
        if not isinstance(exp, list) or not exp:
            problems.append("experience is missing or empty: add at least one job.")
        else:
            for n, job in enumerate(exp, 1):
                if not isinstance(job, dict):
                    problems.append(f"experience job {n} is not laid out as title / company / dates / bullets.")
                    continue
                for key in ("title", "company", "dates"):
                    if not str(job.get(key) or "").strip():
                        problems.append(f"experience job {n} ({job.get('title', '?')}): {key} is empty.")
                bullets = job.get("bullets")
                if not isinstance(bullets, list) or not bullets:
                    problems.append(f"experience job {n} ({job.get('title', '?')}): no bullets.")
                else:
                    for b, item in enumerate(bullets, 1):
                        if not (isinstance(item, dict) and str(item.get("text") or "").strip()):
                            problems.append(f"experience job {n}, bullet {b}: needs  - text: your sentence")
        if not isinstance(data.get("skills"), dict) or not data.get("skills"):
            problems.append("skills is missing: list your skills by group (Languages: [SQL, Python] ...).")
        if not isinstance(data.get("education"), list) or not data.get("education"):
            problems.append("education is missing or empty.")
    else:
        search = data.get("search") or {}
        if not search.get("title_keywords"):
            problems.append("search > title_keywords is empty: add the job titles you want.")
        if not (data.get("sources") or {}):
            problems.append("sources is missing: copy that part from config.example.yaml.")
        years = search.get("max_years_experience", 3)
        if not isinstance(years, int) or not 0 <= years <= 30:
            problems.append(f"search > max_years_experience must be a whole number like 2 (now: {years!r}).")
        score = search.get("min_score", 55)
        if not isinstance(score, (int, float)) or not 0 <= score <= 100:
            problems.append(f"search > min_score must be a number from 0 to 100 (now: {score!r}).")
        from .filters import COUNTRY_PLACES
        countries = search.get("also_countries") or []
        if not isinstance(countries, list):
            problems.append("search > also_countries must be a list, e.g.  also_countries: [india]")
        else:
            for c in countries:
                if str(c).strip().lower() not in COUNTRY_PLACES:
                    problems.append(f"search > also_countries: '{c}' is not known. Use one of: "
                                    + ", ".join(sorted(COUNTRY_PLACES)) + ".")
        level = str(search.get("german_level", "A2")).upper()
        if level not in ("A1", "A2", "B1", "B2", "C1", "C2"):
            problems.append(f"search > german_level must be A1, A2, B1, B2, C1 or C2 (now: {level}).")

    if problems:
        return False, [f"{name} can be read, but these parts need attention:"] + [f"   - {p}" for p in problems]
    return True, [f"OK: {name} is fine. Job Hunter can read it."]


def check_file(path: Path) -> tuple[bool, list[str]]:
    if not path.exists():
        return False, [f"PROBLEM: {path} does not exist."]
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, [f"PROBLEM: {path.name} is not saved as UTF-8. In Notepad: File > Save as > Encoding: UTF-8."]
    return check_text(text, path.name)


def run(paths: list[str], config_dir: Path) -> int:
    files = [Path(p.strip('"')) for p in paths] or [config_dir / "profile.yaml", config_dir / "config.yaml"]
    all_ok = True
    for path in files:
        ok, report = check_file(path)
        all_ok &= ok
        print(f"\n=== {path} ===")
        print("\n".join(report))
    print("\nEverything is fine." if all_ok else "\nSome things need fixing (see above).")
    return 0 if all_ok else 1
