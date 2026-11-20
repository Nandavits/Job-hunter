"""Jako's laptop page and API. Only reachable from this computer (Docker maps it to 127.0.0.1)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .brain import Brain, as_text
from .team import voices

STATIC = Path(__file__).parent / "static"


class Ask(BaseModel):
    text: str


def create_app(brain: Brain) -> FastAPI:
    app = FastAPI(title="Jako", docs_url=None, redoc_url=None)

    @app.get("/")
    def page():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/ask")
    def ask(body: Ask):
        text = body.text.strip()[:2000]
        if not text:
            return {"reply": "", "parts": []}
        try:
            parts = brain.talk(text, channel="voice")
        except Exception as exc:
            parts = [{"who": "jako", "text": f"Sorry, something went wrong: {exc}"}]
        return {"reply": as_text(parts), "parts": parts}

    hud = {"at": 0.0, "data": {}, "lock": threading.Lock()}

    @app.get("/api/hud")
    def hud_data():
        """Live lines for the side panels; refreshed at most once a minute (email and the laptop are slow)."""
        team_obj = getattr(brain, "team", None)
        if team_obj is None:
            return {}
        with hud["lock"]:
            if time.time() - hud["at"] > 60:
                try:
                    hud["data"] = team_obj.status_lines()
                except Exception as exc:
                    hud["data"] = {"error": str(exc)}
                hud["at"] = time.time()
            return hud["data"]

    @app.get("/api/team")
    def team():
        return voices()

    @app.get("/api/outbox")
    def outbox():
        items = []
        for raw in brain.mem.take_outbox():
            who, text = "jako", raw
            if raw.startswith("[") and "] " in raw:
                who, text = raw[1:raw.index("] ")], raw[raw.index("] ") + 2:]
            items.append({"who": who, "text": text})
        return {"items": items}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app
