"""Background worker: `python -m cdp_core.worker [--once] [--interval 10]`."""

import argparse
import logging
import time

from . import jobs
from .config import settings
from .db import init_db


def main():
    parser = argparse.ArgumentParser(description="CDP Core worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=settings.worker_interval)
    parser.add_argument("--tenant")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    while True:
        result = jobs.run_once(args.tenant)
        logging.getLogger("cdp_core.worker").info("run: %s", result)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
