"""Discover devices and credentials from Home Assistant's Shelly integration.

Reads ``.storage/core.config_entries`` (mounted read-only). This is HA's internal
storage format, not a public API, so parsing is defensive: any surprise yields an
empty list plus a reason, never a crash. Gen1 devices are skipped because this
tool only speaks the Gen2+ RPC API. Disabled entries are skipped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .devices import DeviceEntry


@dataclass
class HaDiscovery:
    entries: list[DeviceEntry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


def discover(path: Path) -> HaDiscovery:
    out = HaDiscovery()
    try:
        doc = json.loads(path.read_text())
        items = doc["data"]["entries"]
    except FileNotFoundError:
        out.error = f"{path} not found (is the Home Assistant config folder mapped?)"
        return out
    except (OSError, ValueError, KeyError, TypeError) as e:
        out.error = f"could not read {path}: {type(e).__name__}"
        return out
    for item in items:
        if not isinstance(item, dict) or item.get("domain") != "shelly":
            continue
        data = item.get("data") or {}
        title = item.get("title") or data.get("host") or "?"
        host = data.get("host")
        if not host:
            out.skipped.append(f"{title}: no host")
            continue
        if item.get("disabled_by"):
            out.skipped.append(f"{title}: disabled in Home Assistant")
            continue
        try:
            gen = int(data.get("gen") or 1)
        except (TypeError, ValueError):
            gen = 1
        if gen < 2:
            out.skipped.append(f"{title}: Gen1 is not supported")
            continue
        port = data.get("port") or 80
        url = f"http://{host}" if int(port) == 80 else f"http://{host}:{port}"
        out.entries.append(DeviceEntry(url=url, password=data.get("password") or "", source="ha"))
    return out
