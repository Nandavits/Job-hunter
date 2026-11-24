"""Puts 'ingest_and_fan_out' on the queue every INGEST_EVERY_MINUTES. Run exactly one of these."""
from __future__ import annotations

import logging
import time

from .settings import ServerSettings
from .tasks import ingest_and_fan_out, queue


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    every = ServerSettings.from_env().ingest_every_minutes * 60
    while True:
        queue().enqueue(ingest_and_fan_out, job_timeout=3600)
        logging.getLogger("jhcloud").info("Queued the shared job search; next in %d minutes", every // 60)
        time.sleep(every)


if __name__ == "__main__":
    main()
