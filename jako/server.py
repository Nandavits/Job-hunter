"""Jako's laptop page and API. Only reachable from this computer (Docker maps it to 127.0.0.1)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import secrets

from fastapi import Body, FastAPI, Header, HTTPException
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

    # ---- the laptop helper (jako/hands.py on Windows) collects Iron Man's jobs here
    def _hands(token: str | None):
        hands = getattr(getattr(brain, "team", None), "hands", None)
        want = getattr(getattr(brain, "cfg", None), "hands_token", "")
        if hands is None or not want or not token or not secrets.compare_digest(token, want):
            raise HTTPException(status_code=401, detail="wrong token")
        return hands

    @app.get("/api/hands/poll")
    def hands_poll(computer: str = "", x_jako_token: str | None = Header(default=None)):
        return _hands(x_jako_token).poll(computer[:80])

    @app.post("/api/hands/result")
    def hands_result(body: dict = Body(...), x_jako_token: str | None = Header(default=None)):
        _hands(x_jako_token).deliver(str(body.get("id", "")), body.get("result") or {})
        return {"ok": True}

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

    live = {"who": "jako", "state": "idle", "text": "", "at": 0.0}

    @app.post("/api/state")
    def set_state(body: dict = Body(...)):
        """The Jako window reports who is speaking, so the live desktop can glow in that colour."""
        who = str(body.get("who", "jako"))
        live.update(who=who if who in voices() else "jako", state=str(body.get("state", "idle"))[:20],
                    text=str(body.get("text", ""))[:300], at=time.time())
        return {"ok": True}

    @app.get("/api/state")
    def get_state():
        return live

    @app.get("/desktop")
    def desktop():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/version")
    def version():
        return {"version": int((STATIC / "index.html").stat().st_mtime)}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app
