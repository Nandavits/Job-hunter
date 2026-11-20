"""Background jobs: reminders when they are due, and the morning briefing."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime

from .brain import Brain
from .telegram_bot import Telegram

log = logging.getLogger("jako")


# How long before the same hardware warning is said again (seconds).
REPEAT = {"battery_low": 20 * 60, "disk_full": 6 * 3600, "disk_low": 24 * 3600, "battery_wear": 7 * 86400,
          "ssd_health": 24 * 3600, "ssd_wear": 7 * 86400, "ssd_hot": 3 * 3600, "memory_full": 7 * 86400}
CHECK_EVERY = 15 * 60


def hardware_alerts(brain: Brain, telegram: Telegram, clock: float | None = None) -> list[str]:
    """Hulk checks battery, disk, drive and memory every 15 minutes and warns (without repeating himself)."""
    cfg, mem = brain.cfg, brain.mem
    clock = clock if clock is not None else time.time()
    state_file = cfg.data_dir / "jako_alerts.json"
    try:
        state = json.loads(state_file.read_text())
    except Exception:
        state = {}
    if "_checked" in state and clock - state["_checked"] < CHECK_EVERY:
        return []
    state["_checked"] = clock
    warnings: list[tuple[str, str]] = []
    hands = getattr(getattr(brain, "team", None), "hands", None)
    if hands is not None and hands.online():
        out = hands.do("hardware_health", {})
        for line in (out.get("text") or "").splitlines():
            m = re.match(r"WARNING \[(\w+)\]: (.+)", line.strip())
            if m:
                warnings.append((m.group(1), m.group(2)))
    from .team import _read_perf, _num
    ram = _num(_read_perf(cfg.data_dir / "perf.csv", 1), "ram")
    if len(ram) >= 12 and sum(ram) / len(ram) >= 85:
        warnings.append(("memory_full", f"Your memory was {sum(ram) / len(ram):.0f}% full on average today. That's what "
                                        "makes the laptop slow. Closing Docker containers you don't need helps, and if "
                                        "your laptop allows it, more RAM would be the best upgrade."))
    said = []
    for key, text in warnings:
        if key in state and clock - state[key] < REPEAT.get(key, 24 * 3600):
            continue
        state[key] = clock
        log.info("Hulk warns (%s): %s", key, text)
        mem.say_later(f"[hulk] {text}")
        telegram.send(f"💪 Hulk: ⚠️ {text}")
        said.append(text)
    state_file.write_text(json.dumps(state))
    return said


def tick(brain: Brain, telegram: Telegram, now: datetime | None = None) -> list[str]:
    """One check. Returns what was announced (for tests)."""
    cfg, mem = brain.cfg, brain.mem
    now = now or datetime.now(cfg.tz)
    said = []
    for r in mem.due_reminders(now):
        text = f"🛡️ Captain America: Reminder: {r['text']}"
        mem.finish_reminder(r["id"])
        mem.say_later(f"[captain_america] Reminder: {r['text']}")
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
    hulk_stamp = cfg.data_dir / "jako_last_health.txt"
    last_health = hulk_stamp.read_text().strip() if hulk_stamp.is_file() else ""
    if now.strftime("%H:%M") >= "19:00" and last_health != today:     # Hulk's daily laptop health report
        hulk_stamp.write_text(today)
        from .team import performance_report
        report = performance_report(cfg.data_dir / "perf.csv", 1)
        if not report.startswith("No performance log"):
            telegram.send(f"💪 Hulk: Laptop health today. {report}")
            mem.say_later(f"[hulk] Laptop health today. {report}")
            said.append(report)
    try:
        said += hardware_alerts(brain, telegram)
    except Exception as exc:
        log.warning("Hardware check failed: %s", exc)
    return said


def run_forever(brain: Brain, telegram: Telegram) -> None:
    while True:
        try:
            tick(brain, telegram)
        except Exception:
            log.exception("Scheduler problem")
        time.sleep(30)
