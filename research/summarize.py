"""Summarise research/raw without printing any secret-looking value."""
import json
import re
from pathlib import Path

raw = Path(__file__).parent / "raw"
SECRETISH = re.compile(r"(pass|key|token|secret|psk|ssl_ca|cert)", re.I)


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


all_methods = {}
for d in sorted(raw.iterdir()):
    load = lambda m: json.loads((d / f"{m}.json").read_text())
    info = load("Shelly.GetDeviceInfo")
    cfg = load("Shelly.GetConfig")
    upd = load("Shelly.CheckForUpdate")
    methods = load("Shelly.ListMethods").get("methods", [])
    all_methods[d.name] = set(methods)
    secret_paths = [p for p, v in walk(cfg) if SECRETISH.search(p) and v not in (None, "", False)]
    print(f"== {d.name} {info.get('id')} {info.get('model')} fw={info.get('ver')} app={info.get('app')} profile={info.get('profile')}")
    print("   config keys:", ", ".join(sorted(cfg)))
    print("   update:", {k: v.get("version") for k, v in upd.items()} if isinstance(upd, dict) else upd)
    print("   methods:", len(methods), "| non-null secret-looking config paths:", secret_paths)

common = set.intersection(*all_methods.values())
print("\nMethods common to all:", len(common))
setconf = sorted(m for m in common if m.endswith("SetConfig"))
print("SetConfig common:", setconf)
for ip, ms in all_methods.items():
    extra = sorted(m for m in ms - common if m.endswith("SetConfig"))
    print(" ", ip, "extra SetConfig:", extra)
print("Update/backup-related:", sorted(m for m in common if re.search(r"Update|Backup|Reboot|FactoryReset|Restore", m)))
