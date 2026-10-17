# Changelog

## 0.5.2

- An update a device reports as already running (Shelly error -106) is now shown as
  **updating** on the overview and device page instead of only being logged. Devices being
  updated are left out of the update dialog, and a second request is refused with a message.
- **Update to stable** shows a result per device: started, in progress, skipped or failed.
- The `httpx` request lines are silenced as soon as the app loads, whatever the log
  configuration. The log's first line names the running version and log level.

## 0.5.1

- Backup push: 10 s connect timeout instead of hanging until the 60 s limit; for GitHub,
  automatic retry through `ssh.github.com:443` when port 22 is blocked; failures name the
  cause (key not accepted, wrong repository, non-empty repository) or report a TCP check of
  the ports.
- Logs: per-request lines (every device HTTP call, every page view) are gone at the default
  level. New **Log level** option; `debug` brings them back.

## 0.5.0

- Device table columns are sortable: click a header (or press Enter on it) to sort, again
  to reverse. Default is IP ascending; the choice is kept across live refreshes and pages.
- **Update to stable** opens a dialog listing every device with a stable update, with its
  current and new version. All are selected by default; untick any to leave them out.

## 0.4.2

- **Rotate deploy key** button on the overview (Home Assistant only): replaces the backup
  repository key with a new one and shows it. Also offers to create the key when a
  repository is set but no key exists yet.

## 0.4.1

- Inside Home Assistant, the header no longer shows the user name.
- The overview shows the backup deploy key and how to add it, and explains how to turn
  the backup on when it is off.

## 0.4.0

- The overview and device pages update by themselves: polls run in the background and
  each device's row or page refreshes as soon as it answers, with a spinner while it is
  being read. No page reload needed.
- After a firmware update the device is re-read at 30 s, 90 s, 3 min and 5 min, and after
  a reboot at 15 s and 45 s, so the new firmware or state appears without a manual poll.
- Fields you are typing in are never overwritten by a refresh.

## 0.3.2

- Added the app icon and logo shown in the Home Assistant app store and app page.

## 0.3.1

- Relicensed from MIT to CC BY-NC-SA 4.0 (non-commercial, share-alike).

## 0.3.0

- Packaged as a Home Assistant app with Ingress: Home Assistant handles login, no port is published.
- Devices and passwords discovered from the Home Assistant Shelly integration; per-device overrides.
- Firmware: choose stable, beta or a specific image URL.

## 0.2.0

- Push configuration history to a private git repository.
- Change the admin password and device passwords in the UI (standalone mode).

## 0.1.0

- Poller, overview, configuration history and diffs, settings editor, firmware updates, reboot.
