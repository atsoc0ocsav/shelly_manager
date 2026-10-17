"""Config snapshots, one JSON file per device, versioned in a local git repo."""
from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
# git@host:path, ssh://[user@]host[:port]/path
_SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?!/)")
_URL_REMOTE = re.compile(r"^ssh://(?:[^@/]+@)?(?P<host>[^:/]+)(?::(?P<port>\d+))?/")
PUSH_TIMEOUT_S = 90
# GitHub serves the same SSH endpoint on 443 for networks that block port 22.
SSH_443_FALLBACK = {"github.com": "ssh.github.com"}
_CONNECT_FAILURES = ("timed out", "Connection refused", "Network is unreachable",
                     "No route to host", "Could not resolve hostname", "Connection closed by remote host")


def remote_host(remote: str) -> tuple[str, int] | None:
    """(host, port) of an SSH remote, or None for anything that is not SSH."""
    m = _URL_REMOTE.match(remote)
    if m:
        return m["host"], int(m["port"] or 22)
    if "://" not in remote:
        m = _SCP_REMOTE.match(remote)
        if m:
            return m["host"], 22
    return None


_ERROR_MARKERS = ("Permission denied", "ERROR:", "ssh:", "Host key verification failed",
                  "rejected", "fatal:")


def _git_error(stderr: str) -> str:
    """The line of git/ssh output that names the cause, not git's generic closing advice."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip() and "Permanently added" not in ln]
    for marker in _ERROR_MARKERS:
        for ln in lines:
            if marker in ln:
                return ln
    return lines[-1] if lines else "push failed"


def tcp_reachable(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
_REV = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass
class Revision:
    rev: str
    date: str
    subject: str


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass
class PushStatus:
    configured: bool = False
    remote: str = ""
    last_attempt: str = ""
    ok: bool | None = None
    message: str = ""


class SnapshotStore:
    def __init__(self, root: Path, remote: str | None = None, ssh_key_file: Path | None = None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        if not (root / ".git").exists():
            self._git("init", "-q", "-b", "main")
            self._git("config", "user.name", "shelly-manager")
            self._git("config", "user.email", "shelly-manager@localhost")
        self._env = dict(os.environ)
        self.push_status = PushStatus(configured=bool(remote), remote=remote or "")
        self._ssh_base = "ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=2"
        if ssh_key_file:
            known_hosts = root.parent / "known_hosts"
            self._ssh_base += (
                f" -i {shlex.quote(str(ssh_key_file))} -o IdentitiesOnly=yes"
                f" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={shlex.quote(str(known_hosts))}"
            )
        self._env["GIT_SSH_COMMAND"] = self._ssh_base
        self._via_443 = False  # sticky once port 22 proved unreachable and 443 worked
        self.remote = remote
        if remote:
            remotes = self._git("remote").split()
            self._git("remote", "set-url" if "origin" in remotes else "add", "origin", remote)

    def _git(self, *args: str, timeout: float | None = None) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                              text=True, env=getattr(self, "_env", None), timeout=timeout).stdout

    def unpushed(self) -> bool:
        if not self.push_status.configured:
            return False
        try:
            head = self._git("rev-parse", "-q", "--verify", "HEAD").strip()
        except subprocess.CalledProcessError:
            return False  # no commits yet
        try:
            pushed = self._git("rev-parse", "-q", "--verify", "refs/remotes/origin/main").strip()
        except subprocess.CalledProcessError:
            return True
        return head != pushed

    def push(self) -> PushStatus:
        """Push main to origin. Never raises; the outcome is recorded in ``push_status``."""
        st = self.push_status
        if not st.configured:
            return st
        st.last_attempt = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.unpushed():
            if st.ok is not False:  # keep a failure visible until a push succeeds
                st.ok, st.message = True, "up to date"
            return st
        target = remote_host(self.remote or "")
        fallback = SSH_443_FALLBACK.get(target[0]) if target and target[1] == 22 else None
        err = self._try_push(via_443=self._via_443 and bool(fallback))
        if err and fallback and not self._via_443 and self._is_connect_failure(err):
            err443 = self._try_push(via_443=True)
            if err443 is None:
                self._via_443, err = True, None
            else:
                err = f"{err}; via {fallback}:443: {err443}"
        if err is None:
            st.ok = True
            st.message = f"pushed (via {fallback}:443, port 22 is blocked)" if self._via_443 else "pushed"
            return st
        st.ok, st.message = False, err + self._reachability_hint(target, fallback, err)
        return st

    def _try_push(self, via_443: bool) -> str | None:
        """Run one push; returns None on success or a one-line error."""
        cmd = self._ssh_base
        if via_443:
            host = remote_host(self.remote or "")[0]
            cmd += f" -o HostName={SSH_443_FALLBACK[host]} -o Port=443"
        self._env["GIT_SSH_COMMAND"] = cmd
        try:
            self._git("push", "-q", "-u", "origin", "main", timeout=PUSH_TIMEOUT_S)
            return None
        except subprocess.CalledProcessError as e:
            return _git_error(e.stderr or "")
        except subprocess.TimeoutExpired:
            return f"push timed out after {PUSH_TIMEOUT_S} s"
        finally:
            self._env["GIT_SSH_COMMAND"] = self._ssh_base

    @staticmethod
    def _is_connect_failure(err: str) -> bool:
        return any(s in err for s in _CONNECT_FAILURES)

    @staticmethod
    def _reachability_hint(target, fallback, err: str) -> str:
        if not target or not SnapshotStore._is_connect_failure(err):
            if "Permission denied" in err:
                return " (the deploy key is not accepted: add it to the repository with write access)"
            if "Repository not found" in err or "does not appear to be a git repository" in err:
                return " (check the repository URL, and that the deploy key is added to that repository)"
            if "rejected" in err or "fetch first" in err:
                return " (the repository already has other history: use an empty repository)"
            return ""
        host, port = target
        checks = [f"{host}:{port} {'reachable' if tcp_reachable(host, port) else 'unreachable'}"]
        if fallback:
            checks.append(f"{fallback}:443 {'reachable' if tcp_reachable(fallback, 443) else 'unreachable'}")
        return " (TCP check from the app: " + ", ".join(checks) + ")"

    def _file(self, device_id: str) -> str:
        if not _SAFE.match(device_id):
            raise ValueError(f"unsafe device id: {device_id!r}")
        return f"{device_id}.json"

    def save(self, device_id: str, snapshot: dict[str, Any], reason: str) -> bool:
        """Write and commit; returns True when the snapshot changed."""
        name = self._file(device_id)
        (self.root / name).write_text(_dump(snapshot))
        self._git("add", name)
        if not self._git("status", "--porcelain", "--", name).strip():
            return False
        self._git("commit", "-q", "-m", f"{device_id}: {reason}", "--", name)
        return True

    def load(self, device_id: str, rev: str | None = None) -> dict[str, Any] | None:
        name = self._file(device_id)
        if rev is None:
            p = self.root / name
            return json.loads(p.read_text()) if p.exists() else None
        if not _REV.match(rev):
            raise ValueError("bad revision")
        return json.loads(self._git("show", f"{rev}:{name}"))

    def history(self, device_id: str, limit: int = 50) -> list[Revision]:
        name = self._file(device_id)
        try:
            out = self._git("log", f"-n{limit}", "--format=%h%x09%cI%x09%s", "--", name)
        except subprocess.CalledProcessError:
            return []
        return [Revision(*line.split("\t", 2)) for line in out.splitlines() if line]

    def diff(self, device_id: str, rev: str) -> str:
        """Diff of one revision against its parent (empty for the first one)."""
        name = self._file(device_id)
        if not _REV.match(rev):
            raise ValueError("bad revision")
        return self._git("show", "--format=", "--no-color", rev, "--", name)
