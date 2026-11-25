"""Loading of config.yaml, profile.yaml and environment variables."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("JOBHUNTER_DATA_DIR", ROOT / "data"))
OUTPUT_DIR = Path(os.environ.get("JOBHUNTER_OUTPUT_DIR", ROOT / "output"))


def _load_dotenv() -> None:
    """Read .env on every run, so a new key or password works without restarting anything.
    Values in .env win, except DATABASE_URL when it is already set (Docker and apply.bat set their own)."""
    try:
        from dotenv import dotenv_values
    except ImportError:  # optional dependency
        return
    path = ROOT / ".env"
    if not path.is_file():
        return
    for key, value in dotenv_values(path).items():
        if value is None or (key == "DATABASE_URL" and os.environ.get(key)):
            continue
        if value.strip() or key not in os.environ:
            os.environ[key] = value


def _read_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        example = CONFIG_DIR / name.replace(".yaml", ".example.yaml")
        if not example.exists():
            raise FileNotFoundError(f"Missing {path}")
        path = example
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise SystemExit(settings_file_error(path, text, exc)) from None


def settings_file_error(path: Path, text: str, exc: Exception) -> str:
    """A plain explanation of a typing mistake in profile.yaml / config.yaml, instead of a long traceback."""
    mark = getattr(exc, "context_mark", None) or getattr(exc, "problem_mark", None)
    lines = text.splitlines()
    msg = [f"\nThere is a typing mistake in {path.name} ({path})."]
    if mark is not None:
        n = mark.line + 1
        shown = lines[mark.line] if mark.line < len(lines) else ""
        msg += [f"Look at line {n}:", f"    {shown}"]
    msg += ["Every line needs a label, a colon and a space, then the value, for example:",
            "    github: https://github.com/yourname",
            "Keep the spaces at the start of each line exactly as in the example file. If you have no value,",
            'write two quotes:  github: ""',
            "Fix the line in Notepad, save, and run the program again."]
    return "\n".join(msg)


def _skill_names(entry) -> list[str]:
    """'ETL/ELT pipelines' -> ['etl', 'elt'];  'SQL (advanced)' -> ['sql'];  'Apache Airflow' -> ['airflow']."""
    text = re.sub(r"\([^)]*\)?|\)", " ", str(entry).lower())
    names = []
    for part in re.split(r"[/,;&]|\band\b", text):
        part = re.sub(r"^\s*apache\s+", "", part)
        part = re.sub(r"\s+(?:pipelines?|tools?|basics|fundamentals)\s*$", "", part)
        part = re.sub(r"\s+", " ", part).strip(" .-:")
        if len(part) >= 2 and len(part.split()) <= 3:
            names.append(part)
    return names


def effective_skills(config_skills: dict, profile: dict) -> dict:
    """Skills used for the match score: the ones in profile.yaml, plus the config.yaml ones.

    config.example.yaml ships with an example skill list. If config.yaml still has exactly that list,
    only the example skills that also appear somewhere in your profile are kept, so the score
    reflects your own CV and not the example.
    """
    skills: dict[str, int] = {}
    groups = profile.get("skills") or {}
    entries = [e for v in groups.values() for e in (v if isinstance(v, list) else [v])] \
        if isinstance(groups, dict) else list(groups)
    for entry in entries:
        for name in _skill_names(entry):
            skills.setdefault(name, 2)
    config_skills = {str(k).lower(): v for k, v in (config_skills or {}).items()}
    if config_skills and config_skills == _example_skills() and skills:
        profile_text = str(profile).lower()
        config_skills = {k: v for k, v in config_skills.items() if k in profile_text}
    for name, weight in config_skills.items():
        skills[name] = weight
    return skills


def _example_list(key: str) -> list:
    try:
        example = yaml.safe_load((CONFIG_DIR / "config.example.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return list((example.get("search") or {}).get(key) or [])


def _example_skills() -> dict:
    try:
        example = yaml.safe_load((CONFIG_DIR / "config.example.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {str(k).lower(): v for k, v in ((example.get("search") or {}).get("skills") or {}).items()}


@dataclass
class Settings:
    config: dict
    profile: dict

    def _from_profile(self) -> dict | None:
        """The search worked out from profile.yaml, when config.yaml does not set its own (see searchplan)."""
        from .searchplan import plan, uses_profile
        search = self.config.get("search", {}) or {}
        if not self.profile or not uses_profile(search, _example_list("title_keywords")):
            return None
        found = plan(self.profile)
        return found if found["title_keywords"] else None

    @property
    def search(self) -> dict:
        search = dict(self.config.get("search", {}))
        mine = self._from_profile()
        if mine:
            search["title_keywords"] = mine["title_keywords"]
            skills = {str(k).lower(): v for k, v in (search.get("skills") or {}).items()}
            if skills == _example_skills():                 # the example's skills are someone else's
                search["skills"] = {}
        search["skills"] = effective_skills(search.get("skills") or {}, self.profile)
        return search

    @property
    def sources(self) -> dict:
        sources = dict(self.config.get("sources", {}))
        mine = self._from_profile()
        if mine and mine["queries"] and isinstance(sources.get("arbeitsagentur"), dict):
            sources["arbeitsagentur"] = {**sources["arbeitsagentur"], "queries": mine["queries"]}
        return sources

    def env(self, key: str, default: str = "") -> str:
        value = (os.environ.get(key, default) or default).strip().strip('"').strip("'")
        if key.endswith("APP_PASSWORD"):
            value = value.replace(" ", "")  # Google shows app passwords as "abcd efgh ijkl mnop"
        return value

    @property
    def database_url(self) -> str:
        url = self.env("DATABASE_URL")
        if url:
            return url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DATA_DIR / 'jobs.db'}"


def load_settings(config: dict | None = None, profile: dict | None = None) -> Settings:
    _load_dotenv()
    return Settings(
        config=config if config is not None else _read_yaml("config.yaml"),
        profile=profile if profile is not None else _read_yaml("profile.yaml"),
    )
