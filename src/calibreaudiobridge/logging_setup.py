"""Logging setup: concise stderr lines + JSON-lines rotating file (cron-friendly)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER_NAME: str = "cab"


class _JsonFormatter(logging.Formatter):
    """Render records as single-line JSON objects for the log file."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Render one record as a JSON line."""
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(*, verbose: bool, log_file: Path | None) -> None:
    """Configure the root 'cab' logger. Safe to call once per process."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_JsonFormatter())
        logger.addHandler(file_handler)
