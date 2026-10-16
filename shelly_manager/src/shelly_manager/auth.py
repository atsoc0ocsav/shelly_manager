"""Authentication and CSRF.

Standalone: one local operator account (bcrypt hash in a file), session in a
signed cookie, CSRF token stored in the session.

Home Assistant (Ingress): HA has already authenticated the user. A request is
accepted only from the Supervisor's address and only when it carries the
``X-Remote-User-Id`` header the Supervisor sets (it strips client-sent copies).
The CSRF token is derived from that user id with the app's secret, so no cookie
is needed. Every state-changing request carries the CSRF token in both modes.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import tempfile
from pathlib import Path

import bcrypt
from fastapi import HTTPException, Request

FAIL_DELAY_S = 1.0


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False  # malformed hash: fail closed


MIN_PASSWORD_LEN = 12


def _current_hash(request: Request) -> str:
    return Path(request.app.state.settings.admin_password_hash_file).read_text().strip()


def _hash_version(password_hash: str) -> str:
    """Bound into every session: changing the password invalidates all existing sessions."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _start_session(request: Request, user: str, password_hash: str) -> None:
    request.session.clear()
    request.session["user"] = user
    request.session["pwv"] = _hash_version(password_hash)
    request.session["csrf"] = secrets.token_urlsafe(32)


async def check_login(request: Request, user: str, password: str) -> bool:
    s = request.app.state.settings
    h = _current_hash(request)
    ok = hmac.compare_digest(user, s.admin_user) & verify_password(password, h)
    if not ok:
        await asyncio.sleep(FAIL_DELAY_S)
        return False
    _start_session(request, user, h)
    return True


def _write_hash(path: Path, password_hash: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(password_hash + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


async def change_password(request: Request, current: str, new: str, repeat: str) -> str | None:
    """Returns an error message, or None on success (and re-issues this session)."""
    h = _current_hash(request)
    if not verify_password(current, h):
        await asyncio.sleep(FAIL_DELAY_S)
        return "Current password is wrong."
    if new != repeat:
        return "The new passwords do not match."
    if len(new) < MIN_PASSWORD_LEN:
        return f"The new password must be at least {MIN_PASSWORD_LEN} characters."
    if new == current:
        return "The new password must differ from the current one."
    new_hash = hash_password(new)
    _write_hash(Path(request.app.state.settings.admin_password_hash_file), new_hash)
    _start_session(request, request.session["user"], new_hash)
    return None


class LoginRequired(Exception):
    pass


def from_trusted_proxy(request: Request) -> bool:
    proxy = request.app.state.settings.trusted_proxy
    return bool(proxy) and request.client is not None and request.client.host == proxy


def ha_user(request: Request) -> tuple[str, str] | None:
    """(user id, display name) from Ingress headers, or None when absent or untrusted."""
    if not from_trusted_proxy(request):
        return None
    uid = request.headers.get("x-remote-user-id", "").strip()
    if not uid:
        return None
    name = (request.headers.get("x-remote-user-display-name")
            or request.headers.get("x-remote-user-name") or uid)
    return uid, name


def csrf_token(request: Request) -> str:
    settings = request.app.state.settings
    if settings.ha_mode:
        u = ha_user(request)
        if not u:
            return ""
        return hmac.new(settings.session_secret.encode(), f"csrf:{u[0]}".encode(), hashlib.sha256).hexdigest()
    return request.session.get("csrf", "")


def current_user(request: Request) -> str:
    if request.app.state.settings.ha_mode:
        u = ha_user(request)
        if not u:
            # No login screen exists in this mode: refuse instead of redirecting.
            raise HTTPException(status_code=403, detail="Open this app from Home Assistant.")
        return u[1]
    user = request.session.get("user")
    if not user or request.session.get("pwv") != _hash_version(_current_hash(request)):
        request.session.clear()
        raise LoginRequired()
    return user


async def require_csrf(request: Request) -> None:
    current_user(request)
    form = await request.form()
    token = form.get("csrf", "")
    expected = csrf_token(request)
    if not expected or not hmac.compare_digest(str(token), expected):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
