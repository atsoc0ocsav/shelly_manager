import asyncio
import re

from fastapi.testclient import TestClient

from shelly_manager import fleet as fleet_mod
from shelly_manager.collector import DeviceState
from shelly_manager.fleet import Fleet
from shelly_manager.store import SnapshotStore

from test_app import PASSWORD, client, login  # noqa: F401  (fixture reuse)


def _fleet(tmp_path, hosts):
    f = tmp_path / "devices.txt"
    f.write_text("".join(f"http://{h} pw\n" for h in hosts))
    return Fleet(f, SnapshotStore(tmp_path / "snap"))


async def test_poll_marks_busy_and_bumps_version_per_device(tmp_path, monkeypatch):
    fl = _fleet(tmp_path, ["10.0.0.1", "10.0.0.2"])
    seen_busy = []
    gate = asyncio.Event()

    async def fake_collect(entry):
        seen_busy.append(set(fl.busy))
        await gate.wait()
        return DeviceState(host=entry.host, url=entry.url, ok=False, error="unreachable",
                           polled_at="2026-10-16T00:00:00+00:00")
    monkeypatch.setattr(fleet_mod, "collect", fake_collect)

    v0 = fl.version
    task = asyncio.create_task(fl.poll_all())
    await asyncio.sleep(0.01)
    assert "*" in fl.busy and {"10.0.0.1", "10.0.0.2"} <= fl.busy
    assert fl.version > v0
    gate.set()
    await task
    assert fl.busy == set()
    assert set(fl.states) == {"10.0.0.1", "10.0.0.2"}
    assert all("*" in b for b in seen_busy)


async def test_start_poll_all_refuses_a_second_concurrent_run(tmp_path, monkeypatch):
    fl = _fleet(tmp_path, ["10.0.0.1"])
    gate = asyncio.Event()

    async def fake_collect(entry):
        await gate.wait()
        return DeviceState(host=entry.host, url=entry.url, ok=False, polled_at="x")
    monkeypatch.setattr(fleet_mod, "collect", fake_collect)
    assert fl.start_poll_all()
    await asyncio.sleep(0.01)
    assert not fl.start_poll_all()
    gate.set()
    await asyncio.sleep(0.05)
    assert fl.busy == set()


async def test_start_poll_one_repeats_at_the_given_delays(tmp_path, monkeypatch):
    fl = _fleet(tmp_path, ["10.0.0.1"])
    calls = []

    async def fake_collect(entry):
        calls.append(asyncio.get_running_loop().time())
        return DeviceState(host=entry.host, url=entry.url, ok=False, polled_at="x")
    monkeypatch.setattr(fleet_mod, "collect", fake_collect)
    fl.start_poll_one("10.0.0.1", delays=(0, 0.05, 0.1))
    await asyncio.sleep(0.3)
    assert len(calls) == 3


def test_api_state_requires_login_and_reports_version(client):  # noqa: F811
    c, _, _ = client
    assert c.get("/api/state", follow_redirects=False).status_code == 303
    login(c)
    r = c.get("/api/state")
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"version", "busy"} and body["busy"] == []


def test_pages_carry_live_regions_and_version(client):  # noqa: F811
    c, _, _ = client
    login(c)
    page = c.get("/").text
    assert re.search(r'data-version="\d+"', page) and 'id="live-overview" data-live' in page
    dev = c.get("/device/10.0.0.5").text
    for region in ("live-summary", "live-firmware", "live-components", "live-history"):
        assert f'id="{region}" data-live' in dev


def test_manual_poll_returns_immediately(client, monkeypatch):  # noqa: F811
    c, _, _ = client
    token = login(c)
    started = []
    monkeypatch.setattr(c.app.state.fleet, "start_poll_all", lambda reason="": started.append(reason) or True)
    monkeypatch.setattr(c.app.state.fleet, "start_poll_one", lambda host, reason="", delays=(0,): started.append(host))
    assert c.post("/poll", data={"csrf": token}, follow_redirects=False).status_code == 303
    assert c.post("/device/10.0.0.5/poll", data={"csrf": token}, follow_redirects=False).status_code == 303
    assert started == ["manual poll", "10.0.0.5"]
