import json
import re

import pytest
from fastapi.testclient import TestClient

from shelly_manager import app as app_mod
from shelly_manager.collector import DeviceState
from shelly_manager.config import Settings
from shelly_manager.ha_source import discover

PROXY = ("172.30.32.2", 50000)
USER = {"X-Remote-User-Id": "u1", "X-Remote-User-Name": "vasco", "X-Remote-User-Display-Name": "Vasco",
        "X-Ingress-Path": "/api/hassio_ingress/TOKEN"}


def _entries(tmp_path):
    p = tmp_path / "core.config_entries"
    p.write_text(json.dumps({"version": 1, "data": {"entries": [
        {"domain": "shelly", "title": "Plus1", "data": {"host": "10.0.0.5", "port": 80, "gen": 2,
                                                         "username": "admin", "password": "ha-pw"}},
        {"domain": "shelly", "title": "Gen1 plug", "data": {"host": "10.0.0.6", "gen": 1}},
        {"domain": "shelly", "title": "Off", "disabled_by": "user", "data": {"host": "10.0.0.7", "gen": 3}},
        {"domain": "shelly", "title": "Custom port", "data": {"host": "10.0.0.8", "port": 8080, "gen": 3}},
        {"domain": "mqtt", "title": "x", "data": {"host": "broker"}},
    ]}}))
    return p


def test_discover_reads_gen2_plus_and_skips_others(tmp_path):
    d = discover(_entries(tmp_path))
    assert [(e.url, e.password, e.source) for e in d.entries] == [
        ("http://10.0.0.5", "ha-pw", "ha"), ("http://10.0.0.8:8080", "", "ha")]
    assert len(d.skipped) == 2 and d.error is None


def test_discover_missing_file_is_an_error_not_a_crash(tmp_path):
    d = discover(tmp_path / "nope")
    assert d.entries == [] and "not found" in d.error


@pytest.fixture
def ha_app(tmp_path):
    (tmp_path / "options.json").write_text(json.dumps({"use_ha_shelly_integration": True}))
    s = Settings.from_ha_options(tmp_path / "options.json", tmp_path, _entries(tmp_path))
    app = app_mod.create_app(s, start_poller=False)
    st = DeviceState(host="10.0.0.5", url="http://10.0.0.5", ok=True, polled_at="2026-10-16T12:00:00+00:00",
                     info={"id": "shellyx-1", "name": "Test", "model": "M", "gen": 2, "ver": "1.7.5", "app": "A"},
                     status={"sys": {}}, updates={}, methods=[], snapshot={"config": {}})
    app.state.fleet.states["10.0.0.5"] = st
    return app, tmp_path


def test_ha_mode_rejects_anything_but_the_supervisor(ha_app):
    app, _ = ha_app
    c = TestClient(app, client=("192.0.2.99", 1))
    assert c.get("/", headers=USER).status_code == 403
    assert c.get("/healthz").status_code == 403


def test_ha_mode_requires_the_user_header(ha_app):
    app, _ = ha_app
    c = TestClient(app, client=PROXY)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 403  # no login screen to redirect to


def test_ha_mode_has_no_login_and_prefixes_urls(ha_app):
    app, _ = ha_app
    c = TestClient(app, client=PROXY)
    page = c.get("/", headers=USER).text
    assert "Vasco" in page and "Log out" not in page
    assert 'href="/api/hassio_ingress/TOKEN/device/10.0.0.5"' in page
    assert 'src="/api/hassio_ingress/TOKEN/static/app.js"' in page
    assert "2 found" in page and "Home Assistant" in page
    assert c.get("/login", headers=USER).status_code == 404
    assert c.get("/account", headers=USER).status_code == 404
    r = c.get("/login", headers=USER)
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


def test_ha_mode_csrf_is_bound_to_the_user(ha_app, monkeypatch):
    app, tmp = ha_app
    c = TestClient(app, client=PROXY)
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/", headers=USER).text).group(1)
    called = []

    async def fake_reboot(entry):
        called.append(entry.host)
    monkeypatch.setattr(app_mod.ops, "reboot", fake_reboot)
    other = dict(USER, **{"X-Remote-User-Id": "u2"})
    assert c.post("/device/10.0.0.5/reboot", data={"csrf": token}, headers=other).status_code == 403
    assert c.post("/device/10.0.0.5/reboot", data={"csrf": token}, headers=USER).status_code == 200
    assert called == ["10.0.0.5"]


def test_ha_password_override_wins_over_integration(ha_app):
    app, tmp = ha_app
    f = app.state.fleet
    assert f.entry("10.0.0.5").password == "ha-pw"
    f.set_password("10.0.0.5", "override")
    assert f.entry("10.0.0.5").password == "override" and f.entry("10.0.0.5").source == "file"
    assert "http://10.0.0.5 override" in (tmp / "devices.txt").read_text()
