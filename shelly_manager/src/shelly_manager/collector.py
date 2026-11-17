"""Read a device's full configuration (config + schedules + webhooks + KVS + scripts)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .client import ShellyClient, ShellyError, ShellyUnreachable
from .devices import DeviceEntry

SCRIPT_CHUNK = 2048
# Shelly.CheckForUpdate makes the device ask Shelly's update server before it answers, so its
# latency is the device's internet path, not the LAN: 5.4-5.9 s warm and over 8 s cold on
# 2026-11-17, while every other call took about 1 s or less. It gets its own timeout, and a
# failure there must not fail the poll (with the 8 s default it made all 17 devices
# "unreachable: ReadTimeout").
CHECK_FOR_UPDATE_TIMEOUT_S = 30.0


@dataclass
class DeviceState:
    host: str
    url: str
    ok: bool = False
    error: str | None = None
    polled_at: str = ""
    info: dict[str, Any] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    updates: dict[str, Any] = field(default_factory=dict)
    methods: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def device_id(self) -> str:
        return self.info.get("id") or self.host

    @property
    def name(self) -> str:
        return self.info.get("name") or self.host


async def _optional(c: ShellyClient, method: str, params: dict | None = None) -> Any:
    """Calls that some firmware lacks: record absence instead of failing the poll."""
    try:
        return await c.rpc(method, params)
    except ShellyError as e:
        return {"_unavailable": str(e)}


async def _check_for_update(c: ShellyClient, status: dict[str, Any]) -> dict[str, Any]:
    """Ask the device for updates; on any failure fall back to what it last learned itself.

    ``sys.available_updates`` in Shelly.GetStatus has the same ``{channel: {"version": ...}}``
    shape and is refreshed by the device on its own schedule.
    """
    try:
        res = await c.rpc("Shelly.CheckForUpdate", timeout=CHECK_FOR_UPDATE_TIMEOUT_S)
        return res if isinstance(res, dict) else {}
    except (ShellyError, ShellyUnreachable) as e:
        sys = status.get("sys") if isinstance(status, dict) else None
        known = sys.get("available_updates") if isinstance(sys, dict) else None
        return {**(known if isinstance(known, dict) else {}), "_unavailable": str(e)}


async def _script_code(c: ShellyClient, script_id: int) -> str:
    code, offset = [], 0
    while True:
        res = await c.rpc("Script.GetCode", {"id": script_id, "offset": offset, "len": SCRIPT_CHUNK})
        chunk = res.get("data", "")
        code.append(chunk)
        offset += len(chunk)
        if not res.get("left") or not chunk:
            return "".join(code)


async def collect(entry: DeviceEntry, transport=None) -> DeviceState:
    state = DeviceState(host=entry.host, url=entry.url,
                        polled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        async with ShellyClient(entry, transport=transport) as c:
            state.info = await c.rpc("Shelly.GetDeviceInfo")
            config = await c.rpc("Shelly.GetConfig")
            # sys.cfg_rev increments on every config save, including no-op saves, so it would
            # commit a snapshot with no real change. It is still visible live in Shelly.GetStatus.
            if isinstance(config.get("sys"), dict):
                config["sys"].pop("cfg_rev", None)
            state.status = await c.rpc("Shelly.GetStatus")
            state.methods = (await c.rpc("Shelly.ListMethods")).get("methods", [])
            state.updates = await _check_for_update(c, state.status)
            scripts = await _optional(c, "Script.List")
            script_code = {}
            for s in scripts.get("scripts", []) if isinstance(scripts, dict) else []:
                script_code[str(s["id"])] = await _script_code(c, s["id"])
            state.snapshot = {
                "device": {k: state.info.get(k) for k in
                           ("id", "mac", "model", "gen", "app", "ver", "fw_id", "profile", "name")},
                "config": config,
                "schedules": await _optional(c, "Schedule.List"),
                "webhooks": await _optional(c, "Webhook.List"),
                "kvs": await _optional(c, "KVS.GetMany"),
                "scripts": script_code,
            }
            state.ok = True
    except (ShellyUnreachable, ShellyError) as e:
        state.error = str(e)
    except Exception as e:  # noqa: BLE001 - one bad device must not stop the fleet poll
        state.error = f"{entry.host}: unexpected {type(e).__name__}: {e}"
    return state


async def collect_all(entries: list[DeviceEntry], concurrency: int = 4) -> list[DeviceState]:
    sem = asyncio.Semaphore(concurrency)

    async def one(e: DeviceEntry) -> DeviceState:
        async with sem:
            return await collect(e)

    return await asyncio.gather(*(one(e) for e in entries))
