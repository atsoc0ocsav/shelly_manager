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
