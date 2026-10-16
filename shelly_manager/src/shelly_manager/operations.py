"""Write operations: component SetConfig, firmware update, reboot."""
from __future__ import annotations

import difflib
import json
from typing import Any
from urllib.parse import urlparse

from .client import ShellyClient
from .devices import DeviceEntry


def setconfig_method(component: str, methods: list[str]) -> tuple[str, int | None]:
    """Map a GetConfig key (``switch:0``, ``sys``, ``em1data:1``) to its SetConfig call.

    Resolved against the device's own ``Shelly.ListMethods`` so per-model coverage
    is whatever the firmware reports, not a hardcoded table.
    """
    ctype, _, cid = component.partition(":")
    wanted = f"{ctype}.setconfig"
    for m in methods:
        if m.lower() == wanted:
            return m, (int(cid) if cid else None)
    raise KeyError(f"device exposes no SetConfig for '{component}'")


def changed_fields(old: Any, new: Any) -> Any:
    """Smallest object that turns ``old`` into ``new`` (dicts recurse, everything else replaces)."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = {}
        for k, v in new.items():
            if k not in old:
                out[k] = v
            elif old[k] != v:
                out[k] = changed_fields(old[k], v)
        return out
    return new


def removed_keys(old: dict, new: dict, prefix: str = "") -> list[str]:
    """Keys present in ``old`` but missing from ``new``; SetConfig cannot delete, so these are rejected."""
    out = []
    for k, v in old.items():
        path = f"{prefix}{k}"
        if k not in new:
            out.append(path)
        elif isinstance(v, dict) and isinstance(new[k], dict):
            out.extend(removed_keys(v, new[k], f"{path}."))
    return out


def pretty_diff(old: Any, new: Any, label: str) -> str:
    a = json.dumps(old, indent=2, sort_keys=True).splitlines()
    b = json.dumps(new, indent=2, sort_keys=True).splitlines()
    return "\n".join(difflib.unified_diff(a, b, f"{label} (device)", f"{label} (proposed)", lineterm=""))


async def apply_setconfig(entry: DeviceEntry, method: str, cid: int | None, patch: dict) -> dict:
    params: dict[str, Any] = {"config": patch}
    if cid is not None:
        params["id"] = cid
    async with ShellyClient(entry) as c:
        return await c.rpc(method, params) or {}


FIRMWARE_HOST_SUFFIXES = (".shelly.cloud",)


def validate_firmware_url(url: str, allow_any_host: bool = False) -> str:
    """A custom image is flashed as-is, so by default only Shelly's own hosts are accepted."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Firmware URL must be an http(s) URL.")
    if not allow_any_host and not u.hostname.endswith(FIRMWARE_HOST_SUFFIXES):
        raise ValueError("Firmware URL must be on a *.shelly.cloud host "
                         "(set SM_ALLOW_ANY_FIRMWARE_URL=true to lift this).")
    return url.strip()


async def firmware_update(entry: DeviceEntry, stage: str = "stable", url: str | None = None) -> Any:
    """Update to a channel the device offers (stable/beta) or to an explicit image URL."""
    if url:
        params: dict[str, Any] = {"url": url}
    elif stage in ("stable", "beta"):
        params = {"stage": stage}
    else:
        raise ValueError("stage must be stable or beta")
    async with ShellyClient(entry) as c:
        return await c.rpc("Shelly.Update", params)


async def reboot(entry: DeviceEntry) -> Any:
    async with ShellyClient(entry) as c:
        return await c.rpc("Shelly.Reboot")
