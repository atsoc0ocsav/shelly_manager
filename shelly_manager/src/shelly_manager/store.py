"""Config snapshots, one JSON file per device, versioned in a local git repo."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
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
        if ssh_key_file:
            known_hosts = root.parent / "known_hosts"
            self._env["GIT_SSH_COMMAND"] = (
                f"ssh -i {shlex.quote(str(ssh_key_file))} -o IdentitiesOnly=yes -o BatchMode=yes "
                f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={shlex.quote(str(known_hosts))}"
            )
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
        try:
            self._git("push", "-q", "-u", "origin", "main", timeout=60)
            st.ok, st.message = True, "pushed"
        except subprocess.CalledProcessError as e:
            lines = (e.stderr or "").strip().splitlines()
            st.ok, st.message = False, lines[-1] if lines else "push failed"
        except subprocess.TimeoutExpired:
            st.ok, st.message = False, "push timed out after 60 s"
        return st

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
