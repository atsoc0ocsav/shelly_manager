# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27,<1"]
# ///
"""Read-only API survey of Shelly Gen2+ devices.

Reads credentials from a "URL password" file, calls only Get/List/Check RPCs,
writes raw responses to research/raw/<ip>/<method>.json and prints a summary.
Never prints credentials.
"""
import json
import sys
from pathlib import Path

import httpx

READ_METHODS = [
    "Shelly.GetDeviceInfo",
    "Shelly.GetConfig",
    "Shelly.GetStatus",
    "Shelly.ListMethods",
    "Shelly.CheckForUpdate",
    "Shelly.GetComponents",
]

cred_file = Path(sys.argv[1]).expanduser()
targets = set(sys.argv[2:])
out_root = Path(__file__).parent / "raw"

creds = {}
for line in cred_file.read_text().splitlines():
    parts = line.split()
    if len(parts) == 2:
        creds[parts[0].rstrip("/")] = parts[1]

for url, pw in creds.items():
    ip = url.split("//")[1]
    if targets and ip.rsplit(".", 1)[1] not in targets:
        continue
    out = out_root / ip
    out.mkdir(parents=True, exist_ok=True)
    auth = httpx.DigestAuth("admin", pw)
    summary = []
    with httpx.Client(auth=auth, timeout=8) as c:
        for m in READ_METHODS:
            try:
                r = c.get(f"{url}/rpc/{m}")
                (out / f"{m}.json").write_text(r.text)
                summary.append(f"{m}={r.status_code}")
            except httpx.HTTPError as e:
                summary.append(f"{m}=ERR({type(e).__name__})")
    print(ip, " ".join(summary))
