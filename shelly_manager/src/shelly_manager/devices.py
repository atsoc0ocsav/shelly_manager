"""Device list: one ``URL password`` pair per line; lines starting with ``#`` are comments.

Only full-line comments are recognised, so a ``#`` inside a password is preserved.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class DeviceEntry:
    url: str
    password: str = ""
    source: str = "file"  # "file" (devices file) or "ha" (Home Assistant Shelly integration)

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or self.url

    def __repr__(self) -> str:  # never leak the password into logs
        return f"DeviceEntry(url={self.url!r})"


def _normalise_url(url: str) -> str:
    url = url.rstrip("/")
    return url if url.startswith(("http://", "https://")) else f"http://{url}"


def _parse_line(line: str) -> DeviceEntry | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    if len(parts) not in (1, 2):
        raise ValueError("expected 'URL [password]'")
    return DeviceEntry(url=_normalise_url(parts[0]), password=parts[1] if len(parts) == 2 else "")


def load_devices(path: Path) -> list[DeviceEntry]:
    entries: list[DeviceEntry] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        try:
            e = _parse_line(raw)
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: {exc}") from None
        if e:
            entries.append(e)
    return entries


def validate_password(password: str) -> None:
    if not password:
        raise ValueError("Password must not be empty.")
    if any(c.isspace() for c in password):
        raise ValueError("Password must not contain spaces; the device file is whitespace-separated.")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def set_password(path: Path, host: str, password: str, url: str | None = None) -> None:
    """Replace one device's password in place, keeping every other line untouched.

    With ``url``, a missing host is appended (an override for a discovered device).
    """
    validate_password(password)
    lines = path.read_text().splitlines() if path.exists() else []
    for i, raw in enumerate(lines):
        e = _parse_line(raw)
        if e and e.host == host:
            lines[i] = f"{e.url} {password}"
            _atomic_write(path, "\n".join(lines) + "\n")
            return
    if url is None:
        raise KeyError(host)
    lines.append(f"{url} {password}")
    _atomic_write(path, "\n".join(lines) + "\n")
