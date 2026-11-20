"""Jako's laptop page and API. Only reachable from this computer (Docker maps it to 127.0.0.1)."""
from __future__ import annotations

import threading
import time
import logging
from pathlib import Path

import secrets

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .brain import Brain, as_text
from .team import voices
from .voice import VoiceGate, decode

STATIC = Path(__file__).parent / "static"


class Ask(BaseModel):
    text: str


class Clip(BaseModel):
    audio: str                      # 16 kHz mono 16-bit, base64 (a few seconds, not kept)
    kind: str = "command"


class Enrol(BaseModel):
    clips: list[str]


log = logging.getLogger("jako.server")


def create_app(brain: Brain, voice: VoiceGate | None = None) -> FastAPI:
    app = FastAPI(title="Jako", docs_url=None, redoc_url=None)
    cfg = getattr(brain, "cfg", None)
    voice = voice or VoiceGate(getattr(cfg, "data_dir", Path("data")))

    # ---- voice check: Jako acts only on its owner's voice
    @app.get("/api/voice")
    def voice_status():
        return voice.status()

    @app.post("/api/voice/check")
    def voice_check(body: Clip):
        try:
            return voice.check(decode(body.audio[:2_000_000]), body.kind)
        except Exception as exc:
            log.exception("voice check failed")
            return {"ok": True, "score": None, "reason": f"check failed: {exc}"}

    @app.post("/api/voice/enroll")
    def voice_enroll(body: Enrol):
        if not voice.available():
            return {"ok": False, "error": "The voice model is missing. Run restart-jako.bat once to install it."}
        return voice.enroll([decode(c[:2_000_000]) for c in body.clips[:6]])

    @app.delete("/api/voice")
    def voice_forget():
        voice.forget()
        return voice.status()

    @app.get("/")
    def page():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/ask")
    def ask(body: Ask):
        text = body.text.strip()[:2000]
        if not text:
            return {"reply": "", "parts": []}
        try:
            parts = (getattr(brain, "talk_soon", None) or brain.talk)(text, channel="voice")
        except Exception as exc:
            log.exception("ask failed: %s", text[:80])
            parts = [{"who": "jako", "text": "Sorry, something went wrong on my side. Please ask me again."}]
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

    # ---- optional background video: put background.mp4 (or .webm) in the data folder
    def _bg_video():
        folder = Path(getattr(cfg, "data_dir", Path("data")))
        for name in ("background.mp4", "background.webm"):
            if (folder / name).is_file():
                return folder / name
        return None

    @app.get("/api/background")
    def background_info():
        return {"video": _bg_video() is not None}

    @app.get("/background-video")
    def background_video():
        f = _bg_video()
        if f is None:
            raise HTTPException(404, "No background video")
        return FileResponse(f, media_type="video/webm" if f.suffix == ".webm" else "video/mp4")

    @app.get("/api/version")
    def version():
        return {"version": int((STATIC / "index.html").stat().st_mtime)}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app
