"""CLI: ``serve`` (default), ``hash-password``, ``poll`` (one-shot snapshot of every device)."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="shelly-manager")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the web UI and poller")
    sub.add_parser("hash-password", help="print a bcrypt hash for SM_ADMIN_PASSWORD_HASH_FILE")
    sub.add_parser("poll", help="snapshot every device once and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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
                proxy_headers=False, server_header=False,
                ssl_certfile=settings.tls_cert_file, ssl_keyfile=settings.tls_key_file)


if __name__ == "__main__":
    main()
