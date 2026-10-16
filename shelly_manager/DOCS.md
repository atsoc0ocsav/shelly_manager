# Shelly Manager

Manage your Shelly Gen2+ devices from Home Assistant: overview, configuration history with
diffs, a settings editor and firmware updates.

## How it finds your devices

With **Use the Home Assistant Shelly integration** on (the default), the app reads the
devices and passwords you already configured in the Shelly integration, from
`.storage/core.config_entries`. Gen1 and disabled entries are skipped; the overview lists
what was skipped and why. New devices in the integration appear on the next poll.

To add a device that is not in the integration, or to use a different password for one
that is, open the device's **Stored password** page. A password saved there overrides the
integration's for that device until you remove the line from `/data/devices.txt`. If a
device password changes, prefer re-authenticating it in the Shelly integration.

## Options

| Option | Default | Meaning |
|---|---|---|
| Use the Home Assistant Shelly integration | on | Discover devices and passwords from the integration |
| Config backup repository | empty | SSH URL of a private git repo to push history to |
| Poll interval (minutes) | 15 | How often every device is read |
| Allow firmware URLs outside shelly.cloud | off | Accept custom firmware image URLs from any host |

### Config backup

1. Create an **empty private** repository.
2. Set **Config backup repository** to its SSH URL, e.g. `git@github.com:you/shelly-configs.git`,
   and restart the app.
3. The app log prints a public key. Add it to the repository as a deploy key **with write
   access**.

The overview shows whether the last push worked. Failed pushes are retried on every poll.
Snapshots never contain WiFi passwords (devices do not return them), but they do contain
your network layout and script code, so keep that repository private.

## Security

- The app is served only through Ingress, and only to Home Assistant administrators. It
  publishes no port and rejects every connection that does not come from the Supervisor.
- Home Assistant authenticates the user; the app has no login of its own in this mode.
  Every change is protected by a CSRF token bound to the Home Assistant user.
- To read the Shelly integration's credentials, the app maps the Home Assistant
  configuration folder **read-only**. That folder also holds every other integration's
  credentials. The app only reads `.storage/core.config_entries` and only uses entries of
  the `shelly` domain. The mapping is part of the app definition, so it exists even with
  the option off; if you do not want the app to have that access at all, run it standalone
  instead (see the repository README).
- Device passwords you enter in the app are stored in `/data/devices.txt` (mode 0600) and
  included in Home Assistant backups.
- Firmware images from URLs are restricted to `*.shelly.cloud` unless you allow otherwise.
