"""The SSH key pair the app uses to push the config history (Home Assistant mode)."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

COMMENT = "shelly-manager@home-assistant"


def public_key(private: Path) -> str | None:
    pub = private.with_suffix(".pub")
    return pub.read_text().strip() if pub.is_file() else None


def rotate(private: Path) -> str:
    """Create a new ed25519 pair and atomically replace the old one; returns the new public key.

    The new pair is generated in the same directory first, so a failure leaves the old
    key in place. The old key stops working only once it is removed from the git host.
    """
    private.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=private.parent, prefix=".keygen.") as tmp:
        new = Path(tmp) / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", COMMENT, "-f", str(new)],
                       check=True, capture_output=True, timeout=30)
        os.chmod(new, 0o600)
        os.replace(new.with_suffix(".pub"), private.with_suffix(".pub"))
        os.replace(new, private)
    return public_key(private) or ""
