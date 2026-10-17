"""Minimal async client for the Shelly Gen2+ JSON-RPC API over HTTP."""
from __future__ import annotations

from typing import Any

import logging

import httpx

# httpx logs every request at INFO ("HTTP Request: POST ... 200 OK"), about seven per device
# per poll. Keep it quiet unless debugging; _setup_logging() lowers this again for debug.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from .devices import DeviceEntry


class ShellyError(RuntimeError):
    """The device answered with a JSON-RPC error (``code`` is the device's error code)."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# Shelly JSON-RPC error codes this app reacts to.
ERR_ALREADY_IN_PROGRESS = -106


class ShellyUnreachable(RuntimeError):
    """Transport-level failure: says nothing about the device's state."""


class ShellyClient:
    def __init__(self, entry: DeviceEntry, timeout: float = 8.0):
        auth = httpx.DigestAuth("admin", entry.password) if entry.password else None
        self.entry = entry
        self._http = httpx.AsyncClient(base_url=entry.url, auth=auth, timeout=timeout)
        self._id = 0

    async def __aenter__(self) -> "ShellyClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def identify(self) -> dict[str, Any]:
        """Unauthenticated ``/shelly`` endpoint."""
        try:
            r = await self._http.get("/shelly", auth=None)
        except httpx.HTTPError as e:
            raise ShellyUnreachable(f"{self.entry.host}: {type(e).__name__}") from e
        r.raise_for_status()
        return r.json()

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._id += 1
        body: dict[str, Any] = {"id": self._id, "method": method}
        if params:
            body["params"] = params
        try:
            r = await self._http.post("/rpc", json=body)
        except httpx.HTTPError as e:
            raise ShellyUnreachable(f"{self.entry.host}: {type(e).__name__}") from e
        if r.status_code == 401:
            raise ShellyError(f"{self.entry.host}: authentication failed (401)")
        data = r.json()
        if "error" in data:
            err = data["error"]
            raise ShellyError(f"{self.entry.host}: {method}: {err.get('code')} {err.get('message')}",
                              code=err.get("code"))
        return data.get("result")
