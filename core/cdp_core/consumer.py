"""Ingest consumer: `python -m cdp_core.consumer [--backend postgres|kafka] [--once]`."""

import argparse
import logging
import time

from . import queue
from .db import init_db


def main():
    parser = argparse.ArgumentParser(description="CDP ingest consumer")
    parser.add_argument("--backend", default=queue.BACKEND, choices=["postgres", "kafka"])
    parser.add_argument("--once", action="store_true", help="drain the queue then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("cdp_core.consumer")
    init_db()
    while True:
        n = queue.consume_kafka() if args.backend == "kafka" else queue.consume_postgres()
        if n:
            log.info("processed %s queued requests", n)
            continue
        if args.once:
            return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
