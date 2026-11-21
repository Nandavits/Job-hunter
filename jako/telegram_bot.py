"""Jako on your phone: a private Telegram chat. Only your own chat id is answered."""
from __future__ import annotations

import logging
import tempfile
import time

import requests

from .brain import Brain
from .config import JakoConfig

log = logging.getLogger("jako")
API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, cfg: JakoConfig, brain: Brain | None = None, session=None):
        self.cfg, self.brain = cfg, brain
        self.http = session or requests.Session()
        self._whisper = None

    def call(self, method: str, **params):
        resp = self.http.post(API.format(token=self.cfg.telegram_token, method=method), data=params, timeout=70)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram error"))
        return data["result"]

    def send(self, text: str, chat_id: str | None = None) -> None:
        chat = chat_id or self.cfg.telegram_chat_id
        if not (self.cfg.telegram_token and chat):
            return
        for i in range(0, len(text), 4000):               # Telegram's message limit
            self.call("sendMessage", chat_id=chat, text=text[i:i + 4000])

    def transcribe(self, file_id: str) -> str:
        info = self.call("getFile", file_id=file_id)
        url = f"https://api.telegram.org/file/bot{self.cfg.telegram_token}/{info['file_path']}"
        audio = self.http.get(url, timeout=60).content
        if self._whisper is None:
            from faster_whisper import WhisperModel
            self._whisper = WhisperModel("base", device="cpu", compute_type="int8",
                                         download_root=str(self.cfg.data_dir / "whisper"))
        with tempfile.NamedTemporaryFile(suffix=".ogg") as f:
            f.write(audio)
            f.flush()
            segments, _ = self._whisper.transcribe(f.name, vad_filter=True)
            return " ".join(s.text.strip() for s in segments).strip()

    def handle(self, message: dict) -> None:
        chat_id = str(message["chat"]["id"])
        if not self.cfg.telegram_chat_id:
            self.send(f"Hi! I'm Jako. To make me answer only you, tell Jako on your laptop:\n\n"
                      f"\"Jako, my Telegram chat id is {chat_id}\"", chat_id)
            return
        if chat_id != str(self.cfg.telegram_chat_id):
            log.warning("Ignored a message from an unknown chat %s", chat_id)
            return
        text = (message.get("text") or "").strip()
        if message.get("voice") or message.get("audio"):
            voice = message.get("voice") or message.get("audio")
            self.call("sendChatAction", chat_id=chat_id, action="typing")
            text = self.transcribe(voice["file_id"])
            if not text:
                self.send("Sorry, I couldn't understand that voice message.")
                return
            self.send(f"🎙 {text}")
        if not text:
            return
        if text in ("/start", "/help"):
            self.send("Hi, I'm Jako, with my team: Iron Man (laptop), Black Widow (email & calendar), Hawkeye (jobs), "
                      "Captain America (reminders & to-dos) and Thor (web & weather). Ask me anything, e.g. "
                      "'what's my day?', 'open my CV folder on the laptop', 'my top jobs'.\n"
                      "/team = everyone reports, /briefing = morning summary, /new = fresh start.")
            return
        if text == "/new":
            self.brain.mem.clear_history("telegram")
            self.send("Fresh start. What can I do for you?")
            return
        self.call("sendChatAction", chat_id=chat_id, action="typing")
        if text == "/team":
            text = "wake up"
        reply = self.brain.briefing() if text == "/briefing" else self.brain.ask(text, channel="telegram")
        self.send(reply)

    def run_forever(self) -> None:
        if not self.cfg.telegram_token:
            log.info("Telegram is off (no TELEGRAM_BOT_TOKEN).")
            return
        offset = None
        log.info("Telegram bot running.")
        while True:
            try:
                updates = self.call("getUpdates", timeout=50, **({"offset": offset} if offset else {}))
                for upd in updates:
                    offset = upd["update_id"] + 1
                    if "message" in upd:
                        try:
                            self.handle(upd["message"])
                        except Exception as exc:
                            log.exception("Telegram message failed")
                            self.send(f"Sorry, something went wrong: {exc}")
            except Exception as exc:
                log.warning("Telegram connection problem: %s", exc)
                time.sleep(10)
