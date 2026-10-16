"""Read a device's full configuration (config + schedules + webhooks + KVS + scripts)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .client import ShellyClient, ShellyError, ShellyUnreachable
from .devices import DeviceEntry

SCRIPT_CHUNK = 2048


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


async def _script_code(c: ShellyClient, script_id: int) -> str:
    code, offset = [], 0
    while True:
        res = await c.rpc("Script.GetCode", {"id": script_id, "offset": offset, "len": SCRIPT_CHUNK})
        chunk = res.get("data", "")
        code.append(chunk)
        offset += len(chunk)
        if not res.get("left") or not chunk:
            return "".join(code)


async def collect(entry: DeviceEntry) -> DeviceState:
    state = DeviceState(host=entry.host, url=entry.url,
                        polled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        async with ShellyClient(entry) as c:
            state.info = await c.rpc("Shelly.GetDeviceInfo")
            config = await c.rpc("Shelly.GetConfig")
            # sys.cfg_rev increments on every config save, including no-op saves, so it would
            # commit a snapshot with no real change. It is still visible live in Shelly.GetStatus.
            if isinstance(config.get("sys"), dict):
                config["sys"].pop("cfg_rev", None)
            state.status = await c.rpc("Shelly.GetStatus")
            state.methods = (await c.rpc("Shelly.ListMethods")).get("methods", [])
            state.updates = await _optional(c, "Shelly.CheckForUpdate")
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
