import re

import pytest
from fastapi.testclient import TestClient

from shelly_manager import app as app_mod
from shelly_manager.auth import hash_password
from shelly_manager.collector import DeviceState
from shelly_manager.config import Settings

PASSWORD = "correct horse battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("shelly_manager.auth.FAIL_DELAY_S", 0)
    devices = tmp_path / "devices.txt"
    devices.write_text("http://10.0.0.5 pw\n")
    hash_file = tmp_path / "admin.hash"
    hash_file.write_text(hash_password(PASSWORD))
    settings = Settings(devices_file=devices, data_dir=tmp_path / "data", bind_host="127.0.0.1",
                        port=0, admin_user="admin", admin_password_hash_file=hash_file,
                        session_secret="x" * 32, poll_interval_s=3600, secure_cookies=False)
    app = app_mod.create_app(settings, start_poller=False)
    st = DeviceState(host="10.0.0.5", url="http://10.0.0.5", ok=True, polled_at="2026-10-13T12:00:00+00:00",
                     info={"id": "shellyx-1", "name": "Test", "model": "M", "gen": 3, "ver": "1.7.5", "app": "A"},
                     status={"sys": {"uptime": 7200}},
                     updates={"stable": {"version": "2.0.1"}},
                     methods=["Switch.SetConfig"],
                     snapshot={"config": {"switch:0": {"name": "old", "auto_off": False}}})
    app.state.fleet.states["10.0.0.5"] = st

    async def fake_poll_one(host, reason="poll"):
        return st
    app.state.fleet.poll_one = fake_poll_one
    return TestClient(app), st, devices


def login(c):
    r = c.post("/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/").text
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


@pytest.mark.parametrize("path", ["/", "/device/10.0.0.5", "/device/10.0.0.5/edit/switch:0"])
def test_anonymous_get_redirects_to_login(client, path):
    c, *_ = client
    r = c.get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/poll", "/update-all", "/device/10.0.0.5/update", "/device/10.0.0.5/reboot"])
def test_anonymous_post_is_denied(client, path):
    c, *_ = client
    assert c.post(path, data={"csrf": "x"}).status_code == 401


def test_wrong_password_rejected(client):
    c, *_ = client
    r = c.post("/login", data={"username": "admin", "password": "nope"})
    assert "Wrong username or password" in r.text
    assert c.get("/", follow_redirects=False).status_code == 303


def test_logged_in_post_without_csrf_is_forbidden(client, monkeypatch):
    c, *_ = client
    login(c)
    called = []
    monkeypatch.setattr(app_mod.ops, "reboot", lambda e: called.append(e))
    assert c.post("/device/10.0.0.5/reboot", data={}).status_code == 403
    assert c.post("/device/10.0.0.5/reboot", data={"csrf": "wrong"}).status_code == 403
    assert not called


def test_overview_shows_update(client):
    c, *_ = client
    login(c)
    page = c.get("/").text
    assert "Test" in page and "stable 2.0.1" in page


def test_edit_preview_then_apply_sends_only_changes(client, monkeypatch):
    c, *_ = client
    token = login(c)
    sent = {}

    async def fake_apply(entry, method, cid, patch):
        sent.update(method=method, cid=cid, patch=patch)
        return {"restart_required": False}
    monkeypatch.setattr(app_mod.ops, "apply_setconfig", fake_apply)
    body = '{"name": "new", "auto_off": false}'
    r = c.post("/device/10.0.0.5/edit/switch:0", data={"csrf": token, "proposed": body, "action": "preview"})
    assert "Change preview" in r.text and not sent
    r = c.post("/device/10.0.0.5/edit/switch:0", data={"csrf": token, "proposed": body, "action": "apply"})
    assert "Change applied" in r.text
    assert sent == {"method": "Switch.SetConfig", "cid": 0, "patch": {"name": "new"}}


def test_edit_rejects_removed_keys(client):
    c, *_ = client
    token = login(c)
    r = c.post("/device/10.0.0.5/edit/switch:0", data={"csrf": token, "proposed": '{"name": "x"}', "action": "apply"})
    assert "cannot be removed" in r.text


def test_security_headers(client):
    c, *_ = client
    r = c.get("/login")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_password_change_invalidates_other_sessions(client):
    c, _, _ = client
    token = login(c)
    other = TestClient(c.app)
    login(other)
    new = "a much better password"
    r = c.post("/account/password", data={"csrf": token, "current": PASSWORD, "new": new, "repeat": new})
    assert "Password changed" in r.text
    assert c.get("/", follow_redirects=False).status_code == 200          # this session re-issued
    assert other.get("/", follow_redirects=False).status_code == 303      # other session dropped
    r = other.post("/login", data={"username": "admin", "password": PASSWORD})
    assert "Wrong username or password" in r.text


def test_password_change_rejects_wrong_current_and_short(client):
    c, _, _ = client
    token = login(c)
    r = c.post("/account/password", data={"csrf": token, "current": "nope", "new": "x" * 12, "repeat": "x" * 12})
    assert "Current password is wrong" in r.text
    r = c.post("/account/password", data={"csrf": token, "current": PASSWORD, "new": "short", "repeat": "short"})
    assert "at least 12" in r.text


def test_device_credentials_tested_before_save(client):
    c, st, devices = client
    token = login(c)
    fleet = c.app.state.fleet
    from shelly_manager.collector import DeviceState as DS

    async def bad(host, pw):
        return DS(host=host, url="", ok=False, error="authentication failed (401)")

    async def good(host, pw):
        return st
    fleet.test_password = bad
    r = c.post("/device/10.0.0.5/credentials", data={"csrf": token, "password": "newpw"})
    assert "did not accept" in r.text and "Save without testing" in r.text
    assert devices.read_text() == "http://10.0.0.5 pw\n"
    fleet.test_password = good
    r = c.post("/device/10.0.0.5/credentials", data={"csrf": token, "password": "newpw"}, follow_redirects=False)
    assert r.status_code == 303
    assert devices.read_text() == "http://10.0.0.5 newpw\n"
    assert fleet.entry("10.0.0.5").password == "newpw"


def test_device_credentials_reject_whitespace(client):
    c, _, devices = client
    token = login(c)
    r = c.post("/device/10.0.0.5/credentials", data={"csrf": token, "password": "a b", "action": "force"})
    assert "must not contain spaces" in r.text
    assert devices.read_text() == "http://10.0.0.5 pw\n"


def _fake_update(monkeypatch, calls):
    async def fake(entry, stage="stable", url=None):
        calls.append((stage, url))
    monkeypatch.setattr(app_mod.ops, "firmware_update", fake)


def test_update_to_offered_channel(client, monkeypatch):
    c, _, _ = client
    token = login(c)
    calls = []
    _fake_update(monkeypatch, calls)
    r = c.post("/device/10.0.0.5/update", data={"csrf": token, "target": "stable"})
    assert "updating to stable 2.0.1" in r.text
    assert calls == [("stable", None)]


def test_update_refuses_channel_not_offered(client, monkeypatch):
    c, _, _ = client
    token = login(c)
    calls = []
    _fake_update(monkeypatch, calls)
    r = c.post("/device/10.0.0.5/update", data={"csrf": token, "target": "beta"})
    assert "does not offer a beta update" in r.text and not calls


def test_update_by_url_restricted_to_shelly_hosts(client, monkeypatch):
    c, _, _ = client
    token = login(c)
    calls = []
    _fake_update(monkeypatch, calls)
    r = c.post("/device/10.0.0.5/update", data={"csrf": token, "target": "url", "url": "https://evil.example/fw.zip"})
    assert "shelly.cloud" in r.text and not calls
    good = "https://fwcdn.shelly.cloud/gen2/Mini1G4/abc"
    c.post("/device/10.0.0.5/update", data={"csrf": token, "target": "url", "url": good})
    assert calls == [("stable", good)]


def test_device_page_explains_latest_stable(client):
    c, st, _ = client
    login(c)
    st.updates = {"beta": {"version": "2.2.0-beta1"}}
    page = c.get("/device/10.0.0.5").text
    assert "latest <strong>stable</strong>" in page and "2.2.0-beta1" in page


def _two_device_fleet(c, st):
    from shelly_manager.collector import DeviceState
    from shelly_manager.devices import DeviceEntry
    f = c.app.state.fleet
    other = DeviceState(host="10.0.0.6", url="http://10.0.0.6", ok=True, polled_at="2026-10-16T12:00:00+00:00",
                        info={"id": "shellyx-2", "name": "Other", "model": "M", "gen": 3, "ver": "1.7.4", "app": "A"},
                        status={"sys": {}}, updates={"stable": {"version": "2.0.1"}}, methods=[], snapshot={"config": {}})
    f.entries["10.0.0.6"] = DeviceEntry(url="http://10.0.0.6", password="pw")
    f.states["10.0.0.6"] = other


def test_update_all_updates_only_selected_devices(client, monkeypatch):
    c, st, _ = client
    token = login(c)
    _two_device_fleet(c, st)
    calls = []

    async def fake(entry, stage="stable", url=None):
        calls.append((entry.host, stage))
    monkeypatch.setattr(app_mod.ops, "firmware_update", fake)
    monkeypatch.setattr(c.app.state.fleet, "start_poll_one", lambda *a, **k: None)
    r = c.post("/update-all", data={"csrf": token, "hosts": ["10.0.0.6"]})
    assert calls == [("10.0.0.6", "stable")] and "Other" in r.text


def test_update_all_with_nothing_selected_does_nothing(client, monkeypatch):
    c, st, _ = client
    token = login(c)
    calls = []
    monkeypatch.setattr(app_mod.ops, "firmware_update", lambda *a, **k: calls.append(a))
    r = c.post("/update-all", data={"csrf": token})
    assert "Nothing selected" in r.text and not calls


def test_update_all_skips_a_device_that_no_longer_offers_stable(client, monkeypatch):
    c, st, _ = client
    token = login(c)
    st.updates = {"beta": {"version": "2.2.0-beta1"}}
    calls = []
    monkeypatch.setattr(app_mod.ops, "firmware_update", lambda *a, **k: calls.append(a))
    r = c.post("/update-all", data={"csrf": token, "hosts": ["10.0.0.5"]})
    assert ">skipped<" in r.text and "no stable update offered" in r.text and "10.0.0.5" in r.text and not calls


def test_overview_has_sortable_headers_and_update_dialog(client):
    c, st, _ = client
    login(c)
    _two_device_fleet(c, st)
    page = c.get("/").text
    assert 'data-sort-key="ip"' in page and 'aria-sort="ascending"><button type="button" class="th-sort" data-sort-key="ip"' in page
    assert page.count('scope="col" aria-sort="none"') == 8
    assert '<dialog id="update-dialog"' in page and "Update to stable (2)" in page
    assert page.count('name="hosts"') == 2
    assert "<td>1.7.4</td><td><strong>2.0.1</strong></td>" in page


def test_sort_keys_order_versions_and_ips():
    from shelly_manager.app import _ip_key, _version_key
    assert _ip_key("10.0.0.9") < _ip_key("10.0.0.10") < _ip_key("host.lan")
    assert _version_key("1.7.5") < _version_key("2.0.1") < _version_key("2.1.0-beta2") < _version_key("2.1.0")


def test_update_already_in_progress_is_shown_not_just_logged(client, monkeypatch):
    from shelly_manager.client import ShellyError
    c, st, _ = client
    token = login(c)
    monkeypatch.setattr(c.app.state.fleet, "start_poll_one", lambda *a, **k: None)

    async def busy(entry, stage="stable", url=None):
        raise ShellyError("10.0.0.5: Shelly.Update: -106 Already in progress", code=-106)
    monkeypatch.setattr(app_mod.ops, "firmware_update", busy)
    r = c.post("/device/10.0.0.5/update", data={"csrf": token, "target": "stable"})
    assert "Update already in progress" in r.text
    assert "10.0.0.5" in c.app.state.fleet.updating
    page = c.get("/").text
    assert "updating to" in page and "All on latest stable" in page  # excluded from the dialog
    assert "Update in progress" in c.get("/device/10.0.0.5").text


def test_second_update_request_is_refused_while_updating(client, monkeypatch):
    c, st, _ = client
    token = login(c)
    monkeypatch.setattr(c.app.state.fleet, "start_poll_one", lambda *a, **k: None)
    calls = []

    async def ok(entry, stage="stable", url=None):
        calls.append(stage)
    monkeypatch.setattr(app_mod.ops, "firmware_update", ok)
    assert "Firmware update started" in c.post("/device/10.0.0.5/update", data={"csrf": token, "target": "stable"}).text
    assert "already updating" in c.post("/device/10.0.0.5/update", data={"csrf": token, "target": "stable"}).text
    r = c.post("/update-all", data={"csrf": token, "hosts": ["10.0.0.5"]})
    assert "in progress" in r.text and "0 of 1" in r.text
    assert calls == ["stable"]


def test_update_marker_clears_when_the_version_changes(client):
    from shelly_manager.collector import DeviceState
    c, st, _ = client
    f = c.app.state.fleet
    f.mark_updating("10.0.0.5", "stable 2.0.1")
    new = DeviceState(host="10.0.0.5", url=st.url, ok=True, polled_at="x", info=dict(st.info, ver="2.0.1"),
                      status=st.status, updates={}, methods=[], snapshot=st.snapshot)
    f._record(new, "after firmware update")
    assert "10.0.0.5" not in f.updating


def test_update_all_reports_each_device(client, monkeypatch):
    from shelly_manager.client import ShellyError
    c, st, _ = client
    token = login(c)
    _two_device_fleet(c, st)
    monkeypatch.setattr(c.app.state.fleet, "start_poll_one", lambda *a, **k: None)

    async def mixed(entry, stage="stable", url=None):
        if entry.host == "10.0.0.6":
            raise ShellyError("10.0.0.6: Shelly.Update: -106 Already in progress", code=-106)
    monkeypatch.setattr(app_mod.ops, "firmware_update", mixed)
    r = c.post("/update-all", data={"csrf": token, "hosts": ["10.0.0.5", "10.0.0.6"]})
    assert "1 of 2" in r.text and ">started<" in r.text and ">in progress<" in r.text


def _poll_result(st, ver, uptime, ok=True):
    from shelly_manager.collector import DeviceState
    return DeviceState(host=st.host, url=st.url, ok=ok, polled_at="2026-10-17T15:00:00+00:00",
                       info=dict(st.info, ver=ver), status={"sys": {"uptime": uptime}},
                       updates=st.updates, methods=[], snapshot=st.snapshot)


def test_update_failure_detected_by_restart_on_old_version(client):
    c, st, _ = client
    f = c.app.state.fleet
    f.mark_updating("10.0.0.5", "stable 2.0.1")          # uptime 7200 in the fixture
    f._record(_poll_result(st, "1.7.5", 7300), "poll")   # still running: no verdict
    assert "10.0.0.5" in f.updating
    f._record(_poll_result(st, "1.7.5", 40), "poll")     # rebooted, same version
    assert "10.0.0.5" not in f.updating
    r = f.update_results["10.0.0.5"]
    assert not r.ok and "did not install" in r.message
    login(c)
    assert "update failed" in c.get("/").text
    page = c.get("/device/10.0.0.5").text
    assert "Last firmware update" in page and "failed" in page and "Start update" in page


def test_update_failure_detected_when_no_restart(client, monkeypatch):
    import shelly_manager.fleet as fleet_mod
    c, st, _ = client
    f = c.app.state.fleet
    f.mark_updating("10.0.0.5", "stable 2.0.1")
    monkeypatch.setattr(fleet_mod.time, "monotonic", lambda: f.updating["10.0.0.5"].started + fleet_mod.NO_RESTART_S + 1)
    f._record(_poll_result(st, "1.7.5", 7700), "poll")
    assert "most likely failed" in f.update_results["10.0.0.5"].message


def test_update_success_recorded(client):
    c, st, _ = client
    f = c.app.state.fleet
    f.mark_updating("10.0.0.5", "stable 2.0.1")
    f._record(_poll_result(st, "2.0.1", 30), "poll")
    assert f.update_results["10.0.0.5"].ok and "1.7.5 to 2.0.1" in f.update_results["10.0.0.5"].message


def test_unreachable_while_rebooting_keeps_waiting(client):
    c, st, _ = client
    f = c.app.state.fleet
    f.mark_updating("10.0.0.5", "stable 2.0.1")
    f._record(_poll_result(st, "", 0, ok=False), "poll")
    assert "10.0.0.5" in f.updating


def test_user_can_stop_waiting(client, monkeypatch):
    c, st, _ = client
    token = login(c)
    f = c.app.state.fleet
    monkeypatch.setattr(f, "start_poll_one", lambda *a, **k: None)
    f.mark_updating("10.0.0.5", "stable 2.0.1")
    assert "stop waiting" in c.get("/device/10.0.0.5").text
    assert c.post("/device/10.0.0.5/update/clear", data={"csrf": "bad"}).status_code == 403
    r = c.post("/device/10.0.0.5/update/clear", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and "10.0.0.5" not in f.updating
    assert "Start update" in c.get("/device/10.0.0.5").text
