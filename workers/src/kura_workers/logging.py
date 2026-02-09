"""JSON structured logging for Kura workers.

Controlled via KURA_LOG_FORMAT env var: "json" (default) or "text".
"""

import json
import logging
import traceback
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
        }

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = "".join(traceback.format_exception(*record.exc_info))

        # Include any kura_* extras (handler_name, duration_ms, event_type, user_id, etc.)
        for key, value in record.__dict__.items():
            if key.startswith("kura_"):
                log_entry[key] = value

        return json.dumps(log_entry, default=str)


def setup_logging(log_format: str, level: int = logging.INFO) -> None:
    """Configure root logger with either JSON or plaintext format."""
    import sys

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root.addHandler(handler)
