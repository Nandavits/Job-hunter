"""Companies you never want to see again (e.g. ones that keep reposting ads that are not really open).

Two places, both used: `exclude_companies:` under `search:` in config.yaml, and the "Hide this company"
button in the dashboard (saved in data/hidden-companies.json).
"""
from __future__ import annotations

import json
import re

from .config import DATA_DIR

FILE = None                                   # default: data/hidden-companies.json (tests point it elsewhere)


def _path():
    return FILE or DATA_DIR / "hidden-companies.json"


def norm(name: str) -> str:
    name = re.sub(r"\b(gmbh|mbh|ag|se|kg|ug|inc|ltd|llc|co)\b\.?", " ", (name or "").lower())
    return " ".join(re.findall(r"[a-z0-9äöüß]+", name))


def load() -> list[str]:
    try:
        return list(json.loads(_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def add(name: str) -> None:
    names = load()
    if name and norm(name) not in {norm(n) for n in names}:
        names.append(name.strip())
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(names, indent=1, ensure_ascii=False), encoding="utf-8")


def is_hidden(company: str, cfg: dict | None = None) -> bool:
    c = norm(company)
    if not c:
        return False
    names = load() + list((cfg or {}).get("exclude_companies") or [])
    return any(norm(n) and (norm(n) == c or c.startswith(norm(n) + " ")) for n in names)
