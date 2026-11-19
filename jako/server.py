"""Jako's laptop page and API. Only reachable from this computer (Docker maps it to 127.0.0.1)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .brain import Brain

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
            return {"reply": ""}
        try:
            return {"reply": brain.ask(text, channel="voice")}
        except Exception as exc:
            return {"reply": f"Sorry, something went wrong: {exc}"}

    @app.get("/api/outbox")
    def outbox():
        return {"items": brain.mem.take_outbox()}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app
