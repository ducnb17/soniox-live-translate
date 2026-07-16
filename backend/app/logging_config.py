"""Structured logging setup.

Console rendering in dev, JSON + rotating file in the desktop build (where
there's no console window to read `print()` output).

Log file location:
- Windows: %APPDATA%\\SonioxLiveTranslate\\app.log
- macOS:   ~/Library/Logs/SonioxLiveTranslate/app.log
- Linux:   ${XDG_STATE_HOME:-~/.local/state}/soniox-live-translate/app.log

The first call to `get_logger()` configures the root structlog processor
chain. Call it once at import time from `main.py` (and from `launcher.py`).
"""

import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

APP_NAME = "SonioxLiveTranslate"
_CONFIGURED = False

# structlog stdlib level name → numeric value.
_LEVELS = structlog.stdlib.NAME_TO_LEVEL


def _log_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Logs")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / APP_NAME


def _log_path() -> Path:
    return _log_dir() / "app.log"


def configure_logging(*, file_logging: bool = True, level: str = "INFO") -> None:
    """Configure structlog once. Safe to call multiple times."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # Console: pretty, colored — useful in dev.
    structlog.configure(
        processors=shared_processors + [
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            _LEVELS.get(level.upper(), 20)
        ),
        cache_logger_on_first_use=True,
    )

    if file_logging:
        try:
            log_path = _log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            # File: JSON lines — easy to grep / ship to a log aggregator.
            structlog.configure(
                processors=shared_processors + [
                    structlog.processors.dict_tracebacks,
                    structlog.processors.JSONRenderer(),
                ],
                wrapper_class=structlog.make_filtering_bound_logger(
                    _LEVELS.get(level.upper(), 20)
                ),
                cache_logger_on_first_use=True,
                logger_factory=structlog.WriteLoggerFactory(file=handler.stream),
            )
        except Exception:
            # If we can't write to the log dir (e.g. read-only), fall back to
            # console-only logging silently.
            pass


def get_logger(name: str | None = None):
    """Return a configured structlog logger. Configures on first call."""
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)
