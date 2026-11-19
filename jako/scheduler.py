"""Background jobs: reminders when they are due, and the morning briefing."""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .brain import Brain
from .telegram_bot import Telegram

log = logging.getLogger("jako")


def tick(brain: Brain, telegram: Telegram, now: datetime | None = None) -> list[str]:
    """One check. Returns what was announced (for tests)."""
    cfg, mem = brain.cfg, brain.mem
    now = now or datetime.now(cfg.tz)
    said = []
    for r in mem.due_reminders(now):
        text = f"⏰ Reminder: {r['text']}"
        mem.finish_reminder(r["id"])
        mem.say_later(f"Reminder: {r['text']}")
        telegram.send(text)
        said.append(text)
    stamp = cfg.data_dir / "jako_last_briefing.txt"
    today = now.date().isoformat()
    last = stamp.read_text().strip() if stamp.is_file() else ""
    if now.strftime("%H:%M") >= cfg.briefing_time and last != today and now.hour < 12:
        stamp.write_text(today)
        try:
            text = brain.briefing()
            telegram.send(f"☀️ Good morning!\n\n{text}")
            mem.say_later("Good morning. Your briefing is ready on your phone.")
            said.append(text)
        except Exception as exc:
            log.warning("Briefing failed: %s", exc)
    return said


def run_forever(brain: Brain, telegram: Telegram) -> None:
    while True:
        try:
            tick(brain, telegram)
        except Exception:
            log.exception("Scheduler problem")
        time.sleep(30)
