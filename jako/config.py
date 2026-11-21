"""Jako's settings: everything comes from .env (keys) with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    path = ROOT / ".env"
    if path.is_file():
        for key, value in dotenv_values(path).items():
            if value is None or (key == "DATABASE_URL" and os.environ.get(key)):
                continue
            if value.strip() or key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


@dataclass
class JakoConfig:
    api_key: str = ""
    model: str = "claude-opus-5-5"
    owner_name: str = "Nan"
    city: str = "Berlin"
    latitude: float = 52.52
    longitude: float = 13.405
    timezone: str = "Europe/Berlin"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    gmail_address: str = ""
    gmail_password: str = ""
    calendar_ics_url: str = ""
    jobs_database_url: str = ""
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    briefing_time: str = "08:00"
    meeting_time: str = "18:00"
    team_model: str = "claude-sonnet-5-5"
    hands_url: str = "http://host.docker.internal:8766"
    hands_token: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load() -> JakoConfig:
    _load_env()
    env = os.environ.get
    data_dir = Path(env("JOBHUNTER_DATA_DIR") or ROOT / "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    # data/jako-profile.json (no secrets) wins over .env: the owner's name and which brain Jako and the team use
    try:
        import json
        profile = json.loads((data_dir / "jako-profile.json").read_text(encoding="utf-8"))
    except Exception:
        profile = {}
    return JakoConfig(
        api_key=env("ANTHROPIC_API_KEY", ""),
        model=profile.get("model") or env("JAKO_MODEL", "claude-opus-5-5"),
        owner_name=profile.get("owner_name") or env("JAKO_OWNER_NAME", "Nan"),
        city=env("JAKO_CITY", "Berlin"),
        latitude=float(env("JAKO_LATITUDE", "52.52")),
        longitude=float(env("JAKO_LONGITUDE", "13.405")),
        timezone=env("JAKO_TIMEZONE", "Europe/Berlin"),
        telegram_token=env("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
        gmail_address=env("GMAIL_ADDRESS", ""),
        gmail_password=(env("GMAIL_APP_PASSWORD", "") or "").replace(" ", ""),
        calendar_ics_url=env("GOOGLE_CALENDAR_ICS_URL", ""),
        jobs_database_url=env("DATABASE_URL", ""),
        data_dir=data_dir,
        briefing_time=env("JAKO_BRIEFING_TIME", "08:00"),
        meeting_time=env("JAKO_MEETING_TIME", "18:00"),
        team_model=profile.get("team_model") or env("JAKO_TEAM_MODEL", "") or "claude-sonnet-5-5",
        hands_url=env("JAKO_HANDS_URL", "http://host.docker.internal:8766"),
        hands_token=env("JAKO_HANDS_TOKEN", ""),
    )
