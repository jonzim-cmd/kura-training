"""Kura Workers — Background job processor for projections."""

import asyncio
import logging
import sys

from .config import Config
from .registry import registered_types
from .worker import Worker

# Import handlers to register them
from . import handlers  # noqa: F401


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    config = Config.from_env()
    logger = logging.getLogger(__name__)
    logger.info("Registered handlers: %s", registered_types())

    worker = Worker(config)
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
