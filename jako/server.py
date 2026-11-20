"""Jako's laptop page and API. Only reachable from this computer (Docker maps it to 127.0.0.1)."""
from __future__ import annotations

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
