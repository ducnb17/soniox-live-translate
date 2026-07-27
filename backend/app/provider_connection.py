"""Shared helpers for lightweight provider credential checks."""

from typing import Any

import httpx


def response_error(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or "")
        elif error:
            message = str(error)
        message = str(payload.get("message") or message)
    if not message:
        message = response.reason_phrase or "Request failed"
    return f"HTTP {response.status_code}: {message}"


async def test_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str | int] | None = None,
) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.request(method, url, headers=headers, params=params)
        if response.is_success:
            return True, "OK"
        return False, response_error(response)
    except httpx.HTTPError as exc:
        return False, f"Connection error: {exc}"


async def test_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str | int] | None = None,
) -> tuple[bool, str]:
    return await test_request("GET", url, headers=headers, params=params)
