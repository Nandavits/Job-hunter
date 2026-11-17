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
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency
        return
    load_dotenv(ROOT / ".env")


def _read_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        example = CONFIG_DIR / name.replace(".yaml", ".example.yaml")
        if not example.exists():
            raise FileNotFoundError(f"Missing {path}")
        path = example
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


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
