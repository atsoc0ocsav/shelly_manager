# Changelog

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
