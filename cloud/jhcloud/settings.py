"""Server settings from environment variables (12-factor style): the same image runs on a laptop or a server."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONFIG = HERE / "config"


@dataclass
class ServerSettings:
    database_url: str
    redis_url: str
    secret_key: str
    storage_dir: Path
    ingest_every_minutes: int
    secure_cookies: bool
    public_url: str = "http://127.0.0.1:8000"

    @classmethod
    def from_env(cls) -> "ServerSettings":
        return cls(
            database_url=os.environ.get("DATABASE_URL", "sqlite:///jhcloud.db"),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            secret_key=os.environ.get("SECRET_KEY", ""),
            storage_dir=Path(os.environ.get("STORAGE_DIR", str(HERE / "storage"))),
            ingest_every_minutes=int(os.environ.get("INGEST_EVERY_MINUTES", "30")),
            secure_cookies=os.environ.get("SECURE_COOKIES", "0") == "1",
            public_url=os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"),
        )


def default_text(name: str) -> str:
    return (CONFIG / name).read_text(encoding="utf-8")
