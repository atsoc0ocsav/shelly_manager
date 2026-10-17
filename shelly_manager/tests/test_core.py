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


def test_remote_host_parsing():
    from shelly_manager.store import remote_host
    assert remote_host("git@github.com:me/cfg.git") == ("github.com", 22)
    assert remote_host("ssh://git@gitea.lan:2222/me/cfg.git") == ("gitea.lan", 2222)
    assert remote_host("/tmp/bare.git") is None
    assert remote_host("https://github.com/me/cfg.git") is None


def _store_with_commit(tmp_path, remote="git@github.com:me/cfg.git"):
    s = SnapshotStore(tmp_path / "snap", remote=remote)
    s.save("dev-1", {"a": 1}, "first")
    return s


def test_push_falls_back_to_443_and_remembers_it(tmp_path, monkeypatch):
    s = _store_with_commit(tmp_path)
    tried = []

    def fake_try(via_443):
        tried.append(via_443)
        return None if via_443 else "ssh: connect to host github.com port 22: Connection timed out"
    monkeypatch.setattr(s, "_try_push", fake_try)
    st = s.push()
    assert st.ok and "ssh.github.com:443" in st.message and tried == [False, True]
    s.save("dev-1", {"a": 2}, "second")
    tried.clear()
    s.push()
    assert tried == [True]  # goes straight to 443 next time


def test_push_failure_reports_tcp_reachability(tmp_path, monkeypatch):
    import shelly_manager.store as store_mod
    s = _store_with_commit(tmp_path)
    monkeypatch.setattr(s, "_try_push", lambda via_443: "ssh: connect to host github.com port 22: Connection timed out")
    monkeypatch.setattr(store_mod, "tcp_reachable", lambda h, p, timeout=5.0: p == 443)
    st = s.push()
    assert st.ok is False
    assert "github.com:22 unreachable" in st.message and "ssh.github.com:443 reachable" in st.message


def test_push_permission_denied_hint(tmp_path, monkeypatch):
    s = _store_with_commit(tmp_path)
    monkeypatch.setattr(s, "_try_push", lambda via_443: "git@github.com: Permission denied (publickey).")
    st = s.push()
    assert st.ok is False and "deploy key is not accepted" in st.message


def test_ssh_command_has_connect_timeout(tmp_path):
    s = SnapshotStore(tmp_path / "snap", remote="git@github.com:me/cfg.git", ssh_key_file=tmp_path / "k")
    cmd = s._env["GIT_SSH_COMMAND"]
    assert "ConnectTimeout=10" in cmd and "BatchMode=yes" in cmd and "IdentitiesOnly=yes" in cmd


def test_git_error_picks_the_cause_line():
    from shelly_manager.store import _git_error
    out = ("Warning: Permanently added '[ssh.github.com]:443' (ED25519) to the list of known hosts.\n"
           "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.\n\n"
           "Please make sure you have the correct access rights\nand the repository exists.\n")
    assert _git_error(out) == "git@github.com: Permission denied (publickey)."
    assert _git_error("ERROR: Repository not found.\nfatal: Could not read\n") == "ERROR: Repository not found."


def test_httpx_request_logs_are_quiet_by_default():
    import logging
    import shelly_manager.client  # noqa: F401  (sets the levels on import)
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
