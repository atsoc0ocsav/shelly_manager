"""In-memory fleet state plus the polling loop that feeds the snapshot store."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .collector import DeviceState, collect
from .devices import DeviceEntry, load_devices, set_password
from .ha_source import HaDiscovery, discover
from .store import SnapshotStore

log = logging.getLogger(__name__)

# A firmware update normally completes, reboot included, well inside this window.
UPDATE_TIMEOUT_S = 15 * 60


@dataclass
class UpdateInProgress:
    target: str        # "stable 2.0.1", "beta 2.2.0-beta1" or an image URL
    from_version: str
    started: float     # time.monotonic()


class Fleet:
    def __init__(self, devices_file, store: SnapshotStore, ha_config_entries=None):
        self.devices_file = devices_file
        self.store = store
        self.ha_config_entries = ha_config_entries
        self.ha: HaDiscovery | None = None
        self.entries: dict[str, DeviceEntry] = {}
        self.states: dict[str, DeviceState] = {}
        # Change feed for the UI: ``version`` moves whenever anything shown changes,
        # ``busy`` holds hosts being polled right now ("*" while a fleet poll runs).
        self.version = 0
        self.busy: set[str] = set()
        self.updating: dict[str, UpdateInProgress] = {}
        self._tasks: set[asyncio.Task] = set()
        self.reload()

    def bump(self) -> None:
        self.version += 1

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
        if set(entries) != set(self.entries):
            self.bump()
        self.entries = entries

    def entry(self, host: str) -> DeviceEntry:
        if host not in self.entries:
            raise KeyError(host)
        return self.entries[host]

    def mark_updating(self, host: str, target: str) -> None:
        st = self.states.get(host)
        self.updating[host] = UpdateInProgress(target, (st.info.get("ver") if st else "") or "", time.monotonic())
        self.bump()

    def _settle_update(self, st: DeviceState) -> None:
        """Clear the in-progress marker once the version changed or the window has passed."""
        u = self.updating.get(st.host)
        if not u:
            return
        changed = st.ok and st.info.get("ver") and st.info.get("ver") != u.from_version
        if changed or time.monotonic() - u.started > UPDATE_TIMEOUT_S:
            del self.updating[st.host]
            if changed:
                log.info("firmware updated: %s %s -> %s", st.name, u.from_version, st.info.get("ver"))
            else:
                log.warning("firmware update on %s not seen after %d min", st.name, UPDATE_TIMEOUT_S // 60)

    def _record(self, st: DeviceState, reason: str) -> None:
        self.states[st.host] = st
        self._settle_update(st)
        if st.ok:
            if self.store.save(st.device_id, st.snapshot, reason):
                log.info("config changed: %s (%s)", st.name, reason)
        self.bump()

    async def push(self) -> None:
        before = (self.store.push_status.ok, self.store.push_status.message)
        st = await asyncio.to_thread(self.store.push)
        if st.ok is False:
            log.warning("snapshot push failed: %s", st.message)
        if (st.ok, st.message) != before:
            self.bump()

    async def _collect_marked(self, entry: DeviceEntry, reason: str) -> DeviceState:
        self.busy.add(entry.host)
        self.bump()
        try:
            st = await collect(entry)
            self._record(st, reason)
            return st
        finally:
            self.busy.discard(entry.host)
            self.bump()

    async def poll_all(self, reason: str = "poll", concurrency: int = 4) -> None:
        self.reload()  # pick up devices added to or removed from the Shelly integration
        sem = asyncio.Semaphore(concurrency)

        async def one(e: DeviceEntry) -> None:
            async with sem:
                await self._collect_marked(e, reason)

        self.busy.add("*")
        self.bump()
        try:
            # Each device is recorded as soon as it answers, so the UI fills in progressively.
            await asyncio.gather(*(one(e) for e in list(self.entries.values())))
            await self.push()
        finally:
            self.busy.discard("*")
            self.bump()

    async def poll_one(self, host: str, reason: str = "poll") -> DeviceState:
        st = await self._collect_marked(self.entry(host), reason)
        await self.push()
        return st

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)  # keep a reference so the task is not garbage-collected
        task.add_done_callback(self._tasks.discard)

    def start_poll_all(self, reason: str = "manual poll") -> bool:
        """Start a fleet poll in the background; False when one is already running."""
        if "*" in self.busy:
            return False
        self._spawn(self._guarded(self.poll_all(reason)))
        return True

    def start_poll_one(self, host: str, reason: str = "manual poll", delays: tuple[float, ...] = (0,)) -> None:
        """Poll one device in the background, once per delay (seconds from now)."""
        self.entry(host)

        async def later() -> None:
            waited = 0.0
            for d in delays:
                await asyncio.sleep(max(0.0, d - waited))
                waited = d
                if host in self.entries:
                    await self.poll_one(host, reason)

        self._spawn(self._guarded(later()))

    @staticmethod
    async def _guarded(coro) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 - background work must not kill the loop
            log.exception("background poll failed")

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
