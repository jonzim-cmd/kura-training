"""Kura Workers — Background job processor for projections."""

import asyncio
import logging

from .config import Config
from .health import start_health_server
from .logging import setup_logging
from .registry import registered_event_types, registered_types, _projection_handlers
from .worker import Worker

# Import handlers to register them
from . import handlers  # noqa: F401


def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_format)

    logger = logging.getLogger(__name__)

    # Enhanced startup log
    job_types = registered_types()
    event_types = registered_event_types()
    handler_counts = {
        et: len(handlers_list)
        for et, handlers_list in _projection_handlers.items()
    }

    logger.info("Kura worker starting")
    logger.info("Log format: %s", config.log_format)
    logger.info("Health port: %d", config.health_port)
    logger.info("Registered job types: %s", job_types)
    logger.info("Registered event types (%d): %s", len(event_types), handler_counts)

    asyncio.run(_run(config))


async def _run(config: Config) -> None:
    logger = logging.getLogger(__name__)

    # Start health server as background task
    health_server = await start_health_server(config.health_port, config.database_url)
    logger.info("Health server started")

    try:
        worker = Worker(config)
        await worker.run()
    finally:
        health_server.close()
        await health_server.wait_closed()


if __name__ == "__main__":
    main()
