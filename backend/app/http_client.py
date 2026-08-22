"""Shared httpx.AsyncClient pool for all provider HTTP calls.

Each provider previously created a fresh ``httpx.AsyncClient`` per
request, paying TCP + TLS handshake cost every time.  This module
exposes one module-level client (with sane pooling + HTTP/2) that all
providers reuse.  It is created lazily on first use and closed by
``close_http_client()`` during app shutdown (see main.py lifespan).
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            http2=True,
            follow_redirects=True,
        )
    return _client


async def close_http_client() -> None:
    """Close the shared client (call once at app shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
