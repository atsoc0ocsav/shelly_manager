# Shelly Manager

A lightweight Home Assistant app (formerly "add-on") to manage a fleet of Shelly Gen2+
devices from one place: inventory, configuration history with diffs, a settings editor,
and firmware updates. It talks to each device directly over its local RPC API, so devices
need no reconfiguration and nothing goes through the Shelly cloud.

It also runs standalone (outside Home Assistant) with its own login.

Non-commercial use only: see [Licence](#licence).

## Features

- **Discovery from Home Assistant**: Gen2+ devices and their passwords are read from the
  Shelly integration you already have configured. Devices can also be added by hand.
- **Overview**: model, generation, firmware, available stable and beta updates, restart
  flag and last poll for every device.
- **Configuration history**: every poll stores the device config, schedules, webhooks, KVS
  and script code in a local git repository, one JSON file per device. Only real changes are
  committed. Each device page shows its history and the diff of every revision.
- **Off-device backup**: optionally pushes that history to a private git repository after
  every change, using a deploy key the app generates.
- **Settings editor**: edit one component at a time (`sys`, `wifi`, `switch:0`, `light:0`,
  `cover:0`, `em1:0`, ...). The device is re-read before preview and apply, only changed
  fields are sent to `<Component>.SetConfig`, and a reboot is offered when the device
  reports `restart_required`. The editable components come from the device's own
  `Shelly.ListMethods`, so no per-model schema is needed.
- **Firmware**: update a device to the stable or beta release it offers, or to a specific
  firmware image URL (restricted to `*.shelly.cloud` by default); "update all to stable".
- **Device credentials**: override a device password in the UI; it is tested against the
  device before it is saved.

Gen1 devices are not supported.

## Install in Home Assistant

1. **Settings > Apps > App store**, menu **Repositories**, add
   `https://github.com/atsoc0ocsav/shelly_manager`.
2. Install **Shelly Manager**, start it, and open it from the sidebar.

See [shelly_manager/DOCS.md](shelly_manager/DOCS.md) for the options and the security model.

## Run standalone

```sh
cd shelly_manager
uv sync
mkdir -p data
uv run python -c 'import secrets; print(secrets.token_urlsafe(48))' > data/session.secret
uv run shelly-manager hash-password > data/admin.hash
printf 'http://192.0.2.10 device-password\n' > data/devices.txt   # one "URL password" per line
export SM_DEVICES_FILE=data/devices.txt SM_DATA_DIR=data \
       SM_ADMIN_PASSWORD_HASH_FILE=data/admin.hash SM_SESSION_SECRET_FILE=data/session.secret
uv run shelly-manager poll                  # one-shot snapshot, prints a fleet table
SM_PORT=8765 uv run shelly-manager serve    # http://127.0.0.1:8765
```

Standalone mode binds to `127.0.0.1` unless `SM_BIND_HOST` says otherwise, and refuses to
start without the password hash and session secret files. `shelly_manager/deploy/` has an
install script and a hardened systemd unit for a Debian host or LXC.

| Variable | Purpose |
|---|---|
| `SM_DEVICES_FILE` | `URL password` per line; `#` starts a comment line |
| `SM_DATA_DIR` | Snapshots and state |
| `SM_ADMIN_USER`, `SM_ADMIN_PASSWORD_HASH_FILE` | Login (bcrypt hash from `shelly-manager hash-password`) |
| `SM_SESSION_SECRET_FILE` | Signs the session cookie |
| `SM_BIND_HOST`, `SM_PORT` | Listener, default `127.0.0.1:8080` |
| `SM_TLS_CERT_FILE`, `SM_TLS_KEY_FILE` | Serve HTTPS; cookies become `Secure` |
| `SM_SNAPSHOT_REMOTE`, `SM_GIT_SSH_KEY_FILE` | Push history to a git remote |
| `SM_HA_CONFIG_ENTRIES` | Path to a Home Assistant `core.config_entries` to discover devices from |
| `SM_POLL_INTERVAL_S` | Poll interval, default 900 |
| `SM_ALLOW_ANY_FIRMWARE_URL` | Allow firmware URLs outside `*.shelly.cloud` |

## Development

```sh
cd shelly_manager
uv sync
uv run pytest
```

`research/` holds the read-only API survey scripts used to design the tool.

## Licence

Copyright (c) 2026 Vasco Craveiro Costa. Released under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (full text in [LICENSE](LICENSE)):

- **Allowed:** using, sharing and adapting it for non-commercial purposes, with attribution.
- **Required:** adaptations must be distributed under the same licence.
- **Not allowed:** commercial use, including selling it or paid services built on it,
  without written permission. Ask if you have a commercial use in mind.

This is a source-available licence, not an OSI open-source licence. The initial public
commit (`dfc358b`) was published under MIT; every later version is CC BY-NC-SA 4.0 only.

The app is built from third-party components (Python packages, the Home Assistant base
image) under their own licences; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Shelly is a trademark of Shelly Group. This project is not affiliated with, endorsed or
sponsored by Shelly Group or the Home Assistant project.
