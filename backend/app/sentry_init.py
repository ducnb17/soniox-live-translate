"""Sentry error monitoring — initialised exactly once.

Call ``init_sentry()`` early in the process lifecycle (before importing
any route handlers) so that all unhandled exceptions and slow requests
are captured automatically.

Configuration via environment variables or a ``.env`` file:

    SENTRY_DSN          = https://<key>@o<org>.ingest.sentry.io/<project>
    SENTRY_ENVIRONMENT  = production | staging | development  (default: development)
    SENTRY_RELEASE      = 0.4.1  (default: APP_VERSION from version.py)
    SENTRY_TRACES_RATE  = 0.1    (fraction of requests to trace, 0-1, default: 0.1)
    SENTRY_ENABLED      = 1      (set to 0 to disable even if DSN is set)

If ``SENTRY_DSN`` is absent or empty, ``init_sentry()`` is a no-op — the
app runs normally without Sentry.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_INITIALISED = False


def init_sentry() -> None:
    """Initialise the Sentry SDK. Safe to call multiple times."""
    global _INITIALISED
    if _INITIALISED:
        return
    _INITIALISED = True

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("SENTRY_DSN not set — Sentry disabled")
        return

    if os.environ.get("SENTRY_ENABLED", "1").strip() == "0":
        logger.info("Sentry explicitly disabled via SENTRY_ENABLED=0")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        from .version import APP_VERSION

        environment = os.environ.get("SENTRY_ENVIRONMENT", "development")
        release = os.environ.get("SENTRY_RELEASE", f"soniox-live-translate@{APP_VERSION}")
        traces_sample_rate = float(os.environ.get("SENTRY_TRACES_RATE", "0.1"))

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            # --- Integrations ---
            integrations=[
                # Captures FastAPI/Starlette request context (route, method, status).
                # auto_enabling_integrations=True already pulls these in, but being
                # explicit lets us configure them.
                StarletteIntegration(transaction_style="url"),
                FastApiIntegration(transaction_style="url"),
                # Forward Python logging WARNING+ records as Sentry breadcrumbs
                # and ERROR+ as Sentry events.
                LoggingIntegration(
                    level=logging.WARNING,   # breadcrumb threshold
                    event_level=logging.ERROR,  # event threshold
                ),
            ],
            # --- Performance monitoring ---
            traces_sample_rate=traces_sample_rate,
            # Don't profile every traced request — keep overhead low.
            profiles_sample_rate=0.0,
            # --- Privacy / PII ---
            # Strip IPs and user-agents by default; opt-in per event if needed.
            send_default_pii=False,
            # --- Filtering ---
            # Ignore routine WebSocket disconnects from the browser — these are
            # expected (tab closed, network flap) and not actionable.
            ignore_errors=[
                "WebSocketDisconnect",
                "ConnectionClosedOK",
            ],
            # --- Attach structured context ---
            before_send=_before_send,
        )

        logger.info(
            "Sentry initialised  env=%s  release=%s  traces=%.2f",
            environment,
            release,
            traces_sample_rate,
        )

    except ImportError as exc:
        logger.warning("sentry-sdk not installed, skipping Sentry init: %s", exc)
    except Exception as exc:  # noqa: BLE001
        # Never let Sentry initialisation crash the app.
        logger.warning("Sentry init failed (continuing without it): %s", exc)


def _before_send(event: dict, hint: dict) -> dict | None:
    """Filter or enrich events before they are sent to Sentry.

    - Drops events caused by routine client disconnects.
    - Strips any field that looks like an API key from request data.
    """
    # Drop keyboard-interrupt / SIGTERM noise from the process exit.
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type = exc_info[0]
        if exc_type is not None and exc_type.__name__ in {
            "KeyboardInterrupt",
            "SystemExit",
        }:
            return None

    # Scrub sensitive query-string parameters.
    _scrub_request(event)
    return event


def _scrub_request(event: dict) -> None:
    """Remove API keys / tokens from the request payload in-place."""
    _SENSITIVE = frozenset({"api_key", "apikey", "token", "secret", "password"})
    request = event.get("request", {})
    for field in ("query_string", "data"):
        value = request.get(field)
        if isinstance(value, dict):
            for key in list(value):
                if any(s in key.lower() for s in _SENSITIVE):
                    value[key] = "[Filtered]"
