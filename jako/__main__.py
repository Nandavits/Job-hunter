"""Start Jako: the laptop page (http://localhost:8765), the Telegram bot and the reminder clock."""
from __future__ import annotations

import logging
import sys
import threading

from . import config
from .brain import Brain
from .memory import Memory
from .scheduler import run_forever
from .telegram_bot import Telegram


def main() -> None:
    cfg = config.load()
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(),
                                  RotatingFileHandler(cfg.data_dir / "jako.log", maxBytes=1_000_000, backupCount=2)])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    brain = Brain(cfg, Memory(cfg.data_dir / "jako.db"))
    telegram = Telegram(cfg, brain)

    if len(sys.argv) > 1 and sys.argv[1] == "ask":           # quick test: python -m jako ask "hello"
        print(brain.ask(" ".join(sys.argv[2:]), channel="telegram", remember=False))
        return

    threading.Thread(target=telegram.run_forever, daemon=True, name="telegram").start()
    threading.Thread(target=run_forever, args=(brain, telegram), daemon=True, name="clock").start()

    import uvicorn
    from .server import create_app
    uvicorn.run(create_app(brain), host="0.0.0.0", port=8765, log_level="warning")  # nosec B104


if __name__ == "__main__":
    main()
