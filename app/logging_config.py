from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_CONFIGURED = False


def configure_logging() -> None:
    """Set up console + persistent rotating-file logging.

    The file handler writes under .state/, which is bind-mounted outside the
    container (see docker-compose.yml), so failure logs survive rebuilds and
    restarts instead of disappearing with the old container.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = Path(os.getenv("LOG_DIR", ".state"))
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console_handler)
    root.addHandler(file_handler)
