"""In-memory fleet state plus the polling loop that feeds the snapshot store."""
from __future__ import annotations

import asyncio
import logging

from .collector import DeviceState, collect, collect_all
from .devices import DeviceEntry, load_devices, set_password
from .ha_source import HaDiscovery, discover
from .store import SnapshotStore

log = logging.getLogger(__name__)


class Fleet:
    def __init__(self, devices_file, store: SnapshotStore, ha_config_entries=None):
        self.devices_file = devices_file
        self.store = store
        self.ha_config_entries = ha_config_entries
        self.ha: HaDiscovery | None = None
        self.entries: dict[str, DeviceEntry] = {}
        self.states: dict[str, DeviceState] = {}
        self.reload()

    def reload(self) -> None:
        """Merge sources. A line in the devices file overrides the HA entry for the same host."""
        entries: dict[str, DeviceEntry] = {}
        if self.ha_config_entries:
            self.ha = discover(self.ha_config_entries)
            if self.ha.error:
                log.warning("Home Assistant discovery: %s", self.ha.error)
            entries.update({e.host: e for e in self.ha.entries})
        entries.update({e.host: e for e in load_devices(self.devices_file)})
        for gone in set(self.states) - set(entries):
            self.states.pop(gone, None)
        self.entries = entries

    def entry(self, host: str) -> DeviceEntry:
        if host not in self.entries:
            raise KeyError(host)
        return self.entries[host]

    def _record(self, st: DeviceState, reason: str) -> None:
        self.states[st.host] = st
        if st.ok:
            if self.store.save(st.device_id, st.snapshot, reason):
                log.info("config changed: %s (%s)", st.name, reason)

    async def push(self) -> None:
        st = await asyncio.to_thread(self.store.push)
        if st.ok is False:
            log.warning("snapshot push failed: %s", st.message)

    async def poll_all(self, reason: str = "poll") -> None:
        self.reload()  # pick up devices added to or removed from the Shelly integration
        for st in await collect_all(list(self.entries.values())):
            self._record(st, reason)
        await self.push()

    async def poll_one(self, host: str, reason: str = "poll") -> DeviceState:
        st = await collect(self.entry(host))
        self._record(st, reason)
        await self.push()
        return st

    async def test_password(self, host: str, password: str) -> DeviceState:
        """Collect with a candidate password without storing it."""
        return await collect(DeviceEntry(url=self.entry(host).url, password=password))

    def set_password(self, host: str, password: str) -> None:
        """Store in the devices file; for an HA-discovered device this adds an override line."""
        e = self.entry(host)
        set_password(self.devices_file, host, password, url=e.url)
        self.reload()

    async def run(self, interval_s: int) -> None:
        while True:
            try:
                await self.poll_all()
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("fleet poll failed")
            await asyncio.sleep(interval_s)
