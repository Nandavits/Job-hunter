"""Loading of config.yaml, profile.yaml and environment variables."""
from __future__ import annotations

import os
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


@dataclass
class Settings:
    config: dict
    profile: dict

    @property
    def search(self) -> dict:
        return self.config.get("search", {})

    @property
    def sources(self) -> dict:
        return self.config.get("sources", {})

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
