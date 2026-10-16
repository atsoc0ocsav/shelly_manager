import pytest

from shelly_manager import operations as ops
import subprocess

from shelly_manager.devices import load_devices, set_password
from shelly_manager.store import SnapshotStore

METHODS = ["Sys.SetConfig", "Switch.SetConfig", "EM1Data.SetConfig", "BLE.SetConfig", "Shelly.GetConfig"]


@pytest.mark.parametrize("component,expected", [
    ("sys", ("Sys.SetConfig", None)),
    ("switch:1", ("Switch.SetConfig", 1)),
    ("em1data:0", ("EM1Data.SetConfig", 0)),
    ("ble", ("BLE.SetConfig", None)),
])
def test_setconfig_resolution(component, expected):
    assert ops.setconfig_method(component, METHODS) == expected


def test_setconfig_unknown_component():
    with pytest.raises(KeyError):
        ops.setconfig_method("light:0", METHODS)


def test_changed_fields_is_minimal():
    old = {"name": "a", "led": {"mode": "on", "night": False}, "x": 1}
    new = {"name": "a", "led": {"mode": "off", "night": False}, "x": 1}
    assert ops.changed_fields(old, new) == {"led": {"mode": "off"}}


def test_removed_keys_detected():
    assert ops.removed_keys({"a": 1, "b": {"c": 2}}, {"b": {}}) == ["a", "b.c"]


def test_load_devices_skips_comments_and_hides_password(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("# fleet\nhttp://10.0.0.1 s3cret\n\n10.0.0.2\n")
    entries = load_devices(f)
    assert [e.host for e in entries] == ["10.0.0.1", "10.0.0.2"]
    assert entries[1].url == "http://10.0.0.2"
    assert "s3cret" not in repr(entries[0])


def test_store_commits_only_on_change(tmp_path):
    s = SnapshotStore(tmp_path / "snap")
    assert s.save("dev-1", {"a": 1}, "first")
    assert not s.save("dev-1", {"a": 1}, "same")
    assert s.save("dev-1", {"a": 2}, "second")
    hist = s.history("dev-1")
    assert [h.subject for h in hist] == ["dev-1: second", "dev-1: first"]
    assert '"a": 2' in s.diff("dev-1", hist[0].rev)
    assert s.load("dev-1", hist[1].rev) == {"a": 1}


def test_store_rejects_path_traversal(tmp_path):
    s = SnapshotStore(tmp_path / "snap")
    with pytest.raises(ValueError):
        s.save("../evil", {}, "x")
    with pytest.raises(ValueError):
        s.diff("dev-1", "HEAD; rm -rf /")


def test_set_password_keeps_other_lines_and_hash_in_password(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("# fleet\nhttp://10.0.0.1 a#b\nhttp://10.0.0.2 old\n")
    assert load_devices(f)[0].password == "a#b"
    set_password(f, "10.0.0.2", "new#1")
    assert f.read_text() == "# fleet\nhttp://10.0.0.1 a#b\nhttp://10.0.0.2 new#1\n"
    assert oct(f.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(KeyError):
        set_password(f, "10.0.0.9", "x")


def test_store_pushes_only_when_ahead(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    s = SnapshotStore(tmp_path / "snap", remote=str(remote))
    assert s.push().message == "up to date"   # nothing committed yet
    s.save("dev-1", {"a": 1}, "first")
    assert s.unpushed()
    assert s.push().ok is True
    assert not s.unpushed()
    log = subprocess.run(["git", "-C", str(remote), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "dev-1: first" in log


def test_store_push_failure_is_recorded_not_raised(tmp_path):
    s = SnapshotStore(tmp_path / "snap", remote=str(tmp_path / "missing.git"))
    s.save("dev-1", {"a": 1}, "first")
    st = s.push()
    assert st.ok is False and st.message
