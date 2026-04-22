"""Logging configuration for Conductor.

Call `setup_logging()` once at CLI startup. All modules using
`logging.getLogger(__name__)` will inherit the configuration.

Supports:
- Console output: human-readable (default) or JSON structured
- File output: optional, always JSON structured
- Log level: configurable via config or CLI
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class HumanFormatter(logging.Formatter):
    """Human-readable log formatter with emoji prefixes."""

    LEVEL_ICONS = {
        "DEBUG": "🔍",
        "INFO": "▶",
        "WARNING": "⚠",
        "ERROR": "✗",
        "CRITICAL": "🔥",
    }

    def format(self, record: logging.LogRecord) -> str:
        icon = self.LEVEL_ICONS.get(record.levelname, "•")
        timestamp = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).strftime("%H:%M:%S")

        # Include module name for non-conductor loggers
        source = ""
        if record.name and not record.name.startswith("conductor"):
            source = f" [{record.name}]"

        msg = record.getMessage()
        return f"{timestamp} {icon}{source} {msg}"


class JsonFormatter(logging.Formatter):
    """JSON structured log formatter for machine parsing and log files."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        # Include extra fields if set via logger.info("msg", extra={...})
        for key in ("ticket_id", "agent_name", "event", "phase", "step"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_console: bool = False,
) -> None:
    """Configure logging for the entire conductor process.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional path to a log file (always JSON formatted)
        json_console: If True, console output is JSON instead of human-readable
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers (avoid duplicates on re-init)
    root_logger.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    if json_console:
        console.setFormatter(JsonFormatter())
    else:
        console.setFormatter(HumanFormatter())
    root_logger.addHandler(console)

    # File handler (optional, always JSON)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # File gets everything
        file_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
