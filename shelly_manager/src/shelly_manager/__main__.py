"""CLI: ``serve`` (default), ``hash-password``, ``poll`` (one-shot snapshot of every device)."""
from __future__ import annotations

import argparse
import json
import os
import asyncio
import getpass
import logging
import sys

from .config import HA_OPTIONS_FILE


LOG_LEVELS = ("debug", "info", "warning", "error")


def _setup_logging() -> None:
    """App messages at the chosen level; per-request chatter (httpx, access log) only at debug."""
    level = (os.environ.get("SM_LOG_LEVEL") or "").lower()
    if not level and HA_OPTIONS_FILE.exists():
        try:
            level = str(json.loads(HA_OPTIONS_FILE.read_text()).get("log_level", "")).lower()
        except (OSError, ValueError):
            level = ""
    if level not in LOG_LEVELS:
        level = "info"
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    quiet = logging.DEBUG if level == "debug" else logging.WARNING
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(quiet)


def main() -> None:
    parser = argparse.ArgumentParser(prog="shelly-manager")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the web UI and poller")
    sub.add_parser("hash-password", help="print a bcrypt hash for SM_ADMIN_PASSWORD_HASH_FILE")
    sub.add_parser("poll", help="snapshot every device once and exit")
    args = parser.parse_args()
    _setup_logging()

    if args.cmd == "hash-password":
        from .auth import hash_password
        pw = getpass.getpass("Password: ")
        if len(pw) < 12 or pw != getpass.getpass("Repeat: "):
            sys.exit("passwords differ or are shorter than 12 characters")
        print(hash_password(pw))
        return

    from .config import ConfigError, Settings
    try:
        settings = Settings.load()
    except ConfigError as e:
        sys.exit(f"configuration error: {e}")  # fail closed: never start without auth configured

    if args.cmd == "poll":
        from .fleet import Fleet
        from .store import SnapshotStore
        fleet = Fleet(settings.devices_file, SnapshotStore(settings.data_dir / "snapshots",
                                                           settings.snapshot_remote, settings.git_ssh_key_file),
                      settings.ha_config_entries)
        asyncio.run(fleet.poll_all("cli poll"))
        for st in fleet.states.values():
            print(f"{st.host:15} {'ok ' if st.ok else 'ERR'} {st.name:22} {st.info.get('ver', '')} {st.error or ''}")
        ps = fleet.store.push_status
        if ps.configured:
            print(f"push to {ps.remote}: {ps.message or 'nothing to push'}")
        return

    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.port,
                proxy_headers=False, server_header=False, log_config=None,
                access_log=logging.getLogger("uvicorn.access").isEnabledFor(logging.INFO),
                ssl_certfile=settings.tls_cert_file, ssl_keyfile=settings.tls_key_file)


if __name__ == "__main__":
    main()
