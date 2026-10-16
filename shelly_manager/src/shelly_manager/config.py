"""Runtime settings.

Two modes:

* **standalone**: settings from environment variables, own login screen.
* **ha** (Home Assistant app): settings from ``/data/options.json``, served through
  Ingress. Home Assistant authenticates the user; the app only accepts connections
  from the Supervisor and never shows a login.

Secrets are read from files so they never appear in a delivered config.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

HA_OPTIONS_FILE = Path("/data/options.json")
HA_CONFIG_ENTRIES = Path("/homeassistant/.storage/core.config_entries")
HA_INGRESS_PROXY = "172.30.32.2"


class ConfigError(RuntimeError):
    pass


def _secret_path(var: str) -> Path:
    path = os.environ.get(var)
    if not path:
        raise ConfigError(f"{var} is not set")
    p = Path(path).expanduser()
    if not p.is_file():
        raise ConfigError(f"{var} points at a missing file: {p}")
    if not p.read_text().strip():
        raise ConfigError(f"{var} file is empty: {p}")
    return p


def _read_secret_file(var: str) -> str:
    return _secret_path(var).read_text().strip()


def _env_bool(var: str) -> bool:
    return os.environ.get(var, "false").lower() == "true"


def _ensure_secret(path: Path) -> str:
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip()


@dataclass(frozen=True)
class Settings:
    devices_file: Path
    data_dir: Path
    bind_host: str
    port: int
    session_secret: str
    poll_interval_s: int
    secure_cookies: bool
    mode: str = "standalone"
    admin_user: str = "admin"
    admin_password_hash_file: Path | None = None
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    snapshot_remote: str | None = None
    git_ssh_key_file: Path | None = None
    allow_any_firmware_url: bool = False
    ha_config_entries: Path | None = None
    trusted_proxy: str | None = None

    @property
    def ha_mode(self) -> bool:
        return self.mode == "ha"

    @classmethod
    def load(cls) -> "Settings":
        if os.environ.get("SM_MODE") == "ha" or HA_OPTIONS_FILE.exists():
            return cls.from_ha_options()
        return cls.from_env()

    @classmethod
    def from_ha_options(cls, options_file: Path = HA_OPTIONS_FILE, data_dir: Path = Path("/data"),
                        config_entries: Path = HA_CONFIG_ENTRIES) -> "Settings":
        opts = json.loads(options_file.read_text()) if options_file.exists() else {}
        devices = data_dir / "devices.txt"
        if not devices.exists():
            devices.write_text("# Devices not in the Home Assistant Shelly integration, or password overrides.\n"
                               "# One 'URL password' per line.\n")
            devices.chmod(0o600)
        remote = (opts.get("snapshot_remote") or "").strip() or None
        return cls(
            mode="ha",
            devices_file=devices,
            data_dir=data_dir,
            # Inside the app container only: no host port is published, and every
            # request is checked against the Supervisor's address (trusted_proxy).
            bind_host="0.0.0.0",  # noqa: S104
            port=8099,
            session_secret=_ensure_secret(data_dir / "session.secret"),
            poll_interval_s=int(opts.get("poll_interval_minutes", 15)) * 60,
            secure_cookies=False,
            snapshot_remote=remote,
            git_ssh_key_file=data_dir / "deploy_key" if remote else None,
            allow_any_firmware_url=bool(opts.get("allow_any_firmware_url", False)),
            ha_config_entries=config_entries if opts.get("use_ha_shelly_integration", True) else None,
            trusted_proxy=HA_INGRESS_PROXY,
        )

    @classmethod
    def from_env(cls) -> "Settings":
        devices = os.environ.get("SM_DEVICES_FILE")
        if not devices:
            raise ConfigError("SM_DEVICES_FILE is not set")
        ha_entries = os.environ.get("SM_HA_CONFIG_ENTRIES")
        return cls(
            devices_file=Path(devices).expanduser(),
            data_dir=Path(os.environ.get("SM_DATA_DIR", "./data")).expanduser(),
            # Loopback by default; a deployment must opt in to a wider bind.
            bind_host=os.environ.get("SM_BIND_HOST", "127.0.0.1"),
            port=int(os.environ.get("SM_PORT", "8080")),
            admin_user=os.environ.get("SM_ADMIN_USER", "admin"),
            admin_password_hash_file=_secret_path("SM_ADMIN_PASSWORD_HASH_FILE"),
            session_secret=_read_secret_file("SM_SESSION_SECRET_FILE"),
            poll_interval_s=int(os.environ.get("SM_POLL_INTERVAL_S", "900")),
            # Cookies are marked Secure whenever TLS is on, or when forced (TLS terminated upstream).
            secure_cookies=bool(os.environ.get("SM_TLS_CERT_FILE")) or _env_bool("SM_SECURE_COOKIES"),
            tls_cert_file=os.environ.get("SM_TLS_CERT_FILE"),
            tls_key_file=os.environ.get("SM_TLS_KEY_FILE"),
            snapshot_remote=os.environ.get("SM_SNAPSHOT_REMOTE") or None,
            git_ssh_key_file=Path(k).expanduser() if (k := os.environ.get("SM_GIT_SSH_KEY_FILE")) else None,
            allow_any_firmware_url=_env_bool("SM_ALLOW_ANY_FIRMWARE_URL"),
            ha_config_entries=Path(ha_entries).expanduser() if ha_entries else None,
        )
