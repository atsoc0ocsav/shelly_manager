"""Web UI. Every route except /login and /healthz requires an authenticated user.

In Home Assistant mode every request must come from the Supervisor (Ingress), and
all URLs are built under the ``X-Ingress-Path`` prefix.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import deploy_key as dk
from . import operations as ops
from .auth import (LoginRequired, change_password, check_login, csrf_token, current_user,
                   from_trusted_proxy, require_csrf)
from .client import ShellyError, ShellyUnreachable
from .config import Settings
from .fleet import Fleet
from .store import SnapshotStore

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def _available_updates(st) -> dict:
    return {k: v.get("version") for k, v in (st.updates or {}).items()
            if isinstance(v, dict) and v.get("version")}


templates.env.globals["available_updates"] = _available_updates

# Seconds after the action at which the device is re-read, so the UI picks up the result.
AFTER_UPDATE_POLLS = (30, 90, 180, 300)
AFTER_REBOOT_POLLS = (15, 45)


def create_app(settings: Settings, start_poller: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if start_poller:
            task = asyncio.create_task(app.state.fleet.run(settings.poll_interval_s))
        yield
        if task:
            task.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.state.fleet = Fleet(settings.devices_file,
                            SnapshotStore(settings.data_dir / "snapshots", settings.snapshot_remote,
                                          settings.git_ssh_key_file),
                            settings.ha_config_entries)
    if not settings.ha_mode:
        app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                           session_cookie="sm_session", max_age=12 * 3600,
                           same_site="strict", https_only=settings.secure_cookies)

    def base(request: Request) -> str:
        """Ingress prefix, trusted only from the Supervisor; empty in standalone mode."""
        if settings.ha_mode and from_trusted_proxy(request):
            return request.headers.get("x-ingress-path", "").rstrip("/")
        return ""

    def url(request: Request, path: str) -> str:
        return base(request) + path

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if request.method == "GET":
            return RedirectResponse(url(request, "/login"), status_code=303)
        return PlainTextResponse("login required", status_code=401)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        if settings.ha_mode and not from_trusted_proxy(request):
            return PlainTextResponse("Only reachable through Home Assistant Ingress.", status_code=403)
        resp = await call_next(request)
        # Ingress renders the app in a same-origin iframe; standalone forbids framing.
        frame = "'self'" if settings.ha_mode else "'none'"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN" if settings.ha_mode else "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors {frame}")
        return resp

    def fleet(request: Request) -> Fleet:
        return request.app.state.fleet

    def render(request: Request, name: str, **ctx):
        ctx.setdefault("csrf", csrf_token(request))
        if settings.ha_mode:
            try:
                ctx.setdefault("user", current_user(request))
            except HTTPException:
                ctx.setdefault("user", None)
        else:
            ctx.setdefault("user", request.session.get("user"))
        ctx["base"] = base(request)
        ctx["version"] = request.app.state.fleet.version
        ctx["busy"] = request.app.state.fleet.busy
        ctx["ha_mode"] = settings.ha_mode
        ctx["ha"] = request.app.state.fleet.ha
        return templates.TemplateResponse(request, name, ctx)

    def state_or_404(f: Fleet, host: str):
        try:
            f.entry(host)
        except KeyError:
            raise HTTPException(404, "unknown device")
        st = f.states.get(host)
        if st is None:
            raise HTTPException(409, "device not polled yet")
        return st

    @app.get("/api/state")
    async def api_state(request: Request, user: str = Depends(current_user)):
        """Change feed for the live UI: pages refetch themselves when ``version`` moves."""
        f = fleet(request)
        return JSONResponse({"version": f.version, "busy": sorted(f.busy)},
                            headers={"Cache-Control": "no-store"})

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz():
        return "ok"

    def standalone_only():
        if settings.ha_mode:
            raise HTTPException(404, "Home Assistant handles login and accounts.")

    @app.get("/login", response_class=HTMLResponse, dependencies=[Depends(standalone_only)])
    async def login_form(request: Request):
        return render(request, "login.html", error=None)

    @app.post("/login", dependencies=[Depends(standalone_only)])
    async def login(request: Request, username: str = Form(...), password: str = Form(...)):
        if await check_login(request, username, password):
            return RedirectResponse(url(request, "/"), status_code=303)
        return render(request, "login.html", error="Wrong username or password.")

    @app.post("/logout", dependencies=[Depends(standalone_only), Depends(require_csrf)])
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url(request, "/login"), status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request, user: str = Depends(current_user)):
        f = fleet(request)
        rows = [(host, f.states.get(host)) for host in sorted(f.entries, key=lambda h: tuple(int(p) if p.isdigit() else 0 for p in h.split(".")))]
        deploy_key = dk.public_key(settings.git_ssh_key_file) if settings.git_ssh_key_file else None
        return render(request, "overview.html", rows=rows, push=f.store.push_status, entries=f.entries,
                      deploy_key=deploy_key)

    @app.get("/account", response_class=HTMLResponse, dependencies=[Depends(standalone_only)])
    async def account(request: Request, user: str = Depends(current_user)):
        return render(request, "account.html", error=None, done=False)

    @app.post("/account/password", dependencies=[Depends(standalone_only), Depends(require_csrf)])
    async def account_password(request: Request, current: str = Form(...), new: str = Form(...),
                               repeat: str = Form(...)):
        error = await change_password(request, current, new, repeat)
        if error is None:
            log.info("admin password changed; other sessions invalidated")
        return render(request, "account.html", error=error, done=error is None)

    @app.get("/device/{host}/credentials", response_class=HTMLResponse)
    async def credentials_form(request: Request, host: str, user: str = Depends(current_user)):
        f = fleet(request)
        try:
            f.entry(host)
        except KeyError:
            raise HTTPException(404, "unknown device")
        return render(request, "device_password.html", host=host, st=f.states.get(host), error=None, untested=False)

    @app.post("/device/{host}/credentials", dependencies=[Depends(require_csrf)])
    async def credentials_save(request: Request, host: str, password: str = Form(...),
                               action: str = Form("save")):
        f = fleet(request)
        try:
            f.entry(host)
        except KeyError:
            raise HTTPException(404, "unknown device")
        from .devices import validate_password
        try:
            validate_password(password)
        except ValueError as e:
            return render(request, "device_password.html", host=host, st=f.states.get(host), error=str(e), untested=False)
        if action != "force":
            probe = await f.test_password(host, password)
            if not probe.ok:
                return render(request, "device_password.html", host=host, st=f.states.get(host), untested=True,
                              error=f"The device did not accept it: {probe.error}")
        f.set_password(host, password)
        log.info("stored password updated for %s (%s)", host, "untested" if action == "force" else "verified")
        await f.poll_one(host, "credentials updated")
        return RedirectResponse(url(request, f"/device/{host}" if f.states[host].ok else "/"), status_code=303)

    @app.post("/backup/rotate-key", dependencies=[Depends(require_csrf)])
    async def rotate_key(request: Request):
        # Only the app-owned key in HA mode; a standalone key file belongs to the operator.
        if not (settings.ha_mode and settings.git_ssh_key_file):
            raise HTTPException(404, "No app-managed deploy key.")
        try:
            new = await asyncio.to_thread(dk.rotate, settings.git_ssh_key_file)
        except Exception as e:  # noqa: BLE001
            log.exception("deploy key rotation failed")
            return render(request, "message.html", title="Key rotation failed",
                          message=f"The old key is unchanged. {type(e).__name__}: {e}")
        log.info("deploy key rotated: %s", new)
        fleet(request).bump()
        return render(request, "message.html", title="New deploy key created",
                      message="Add the new key below to the backup repository as a deploy key with write "
                              "access, and remove the old one there. Pushes fail until the new key is added.",
                      code=new, back="/")

    @app.post("/poll", dependencies=[Depends(require_csrf)])
    async def poll_all(request: Request):
        fleet(request).start_poll_all("manual poll")  # results stream into the page
        return RedirectResponse(url(request, "/"), status_code=303)

    @app.post("/update-all", dependencies=[Depends(require_csrf)])
    async def update_all(request: Request):
        f = fleet(request)
        started = []
        for host, st in f.states.items():
            if st.ok and "stable" in _available_updates(st):
                try:
                    await ops.firmware_update(f.entry(host), "stable")
                    started.append(st.name)
                    f.start_poll_one(host, "after firmware update", AFTER_UPDATE_POLLS)
                except (ShellyError, ShellyUnreachable) as e:
                    log.warning("update failed: %s", e)
        return render(request, "message.html", title="Firmware updates started",
                      message=f"Stable update started on {len(started)} device(s): {', '.join(started) or 'none'}. "
                              "Devices reboot when done. The device list refreshes by itself as they come back.",
                      back="/")

    @app.get("/device/{host}", response_class=HTMLResponse)
    async def device(request: Request, host: str, user: str = Depends(current_user)):
        f = fleet(request)
        st = state_or_404(f, host)
        history = f.store.history(st.device_id) if st.ok else []
        return render(request, "device.html", st=st, history=history,
                      allow_any_firmware_url=settings.allow_any_firmware_url,
                      config_json=json.dumps(st.snapshot, indent=2, sort_keys=True))

    @app.post("/device/{host}/poll", dependencies=[Depends(require_csrf)])
    async def poll_one(request: Request, host: str):
        fleet(request).start_poll_one(host, "manual poll")  # the page refreshes when it lands
        return RedirectResponse(url(request, f"/device/{host}"), status_code=303)

    @app.get("/device/{host}/rev/{rev}", response_class=HTMLResponse)
    async def revision(request: Request, host: str, rev: str, user: str = Depends(current_user)):
        f = fleet(request)
        st = state_or_404(f, host)
        try:
            diff = f.store.diff(st.device_id, rev)
        except Exception:
            raise HTTPException(404, "unknown revision")
        return render(request, "diff.html", st=st, rev=rev, diff=diff)

    @app.post("/device/{host}/update", dependencies=[Depends(require_csrf)])
    async def update(request: Request, host: str, target: str = Form("stable"), url: str = Form("")):
        f = fleet(request)
        st = state_or_404(f, host)
        offered = _available_updates(st)
        try:
            if target == "url":
                fw_url = ops.validate_firmware_url(url, settings.allow_any_firmware_url)
                await ops.firmware_update(f.entry(host), url=fw_url)
                label = f"the image at {fw_url}"
            elif target in offered:
                await ops.firmware_update(f.entry(host), target)
                label = f"{target} {offered[target]}"
            else:
                raise ValueError(f"The device does not offer a {target} update right now.")
        except (ShellyError, ShellyUnreachable, ValueError) as e:
            return render(request, "message.html", title="Update failed", message=str(e), back=f"/device/{host}")
        log.info("firmware update on %s: %s", st.name, label)
        f.start_poll_one(host, "after firmware update", AFTER_UPDATE_POLLS)
        return render(request, "message.html", title="Firmware update started",
                      message=f"{st.name}: updating to {label}. The device reboots when done; "
                              "its page shows the new version by itself once it is back.",
                      back=f"/device/{host}")

    @app.post("/device/{host}/reboot", dependencies=[Depends(require_csrf)])
    async def do_reboot(request: Request, host: str):
        f = fleet(request)
        st = state_or_404(f, host)
        try:
            await ops.reboot(f.entry(host))
        except (ShellyError, ShellyUnreachable) as e:
            return render(request, "message.html", title="Reboot failed", message=str(e))
        f.start_poll_one(host, "after reboot", AFTER_REBOOT_POLLS)
        return render(request, "message.html", title="Rebooting", message=f"{st.name} is rebooting.",
                      back=f"/device/{host}")

    @app.get("/device/{host}/edit/{component}", response_class=HTMLResponse)
    async def edit_form(request: Request, host: str, component: str, user: str = Depends(current_user)):
        f = fleet(request)
        st = await f.poll_one(host, "read before edit")
        if not st.ok:
            return render(request, "message.html", title="Device unreachable", message=st.error)
        cfg = st.snapshot["config"].get(component)
        if cfg is None:
            raise HTTPException(404, "unknown component")
        try:
            ops.setconfig_method(component, st.methods)
        except KeyError as e:
            return render(request, "message.html", title="Not editable", message=str(e), back=f"/device/{host}")
        return render(request, "edit.html", st=st, component=component,
                      proposed=json.dumps(cfg, indent=2, sort_keys=True), diff=None, error=None)

    @app.post("/device/{host}/edit/{component}", dependencies=[Depends(require_csrf)])
    async def edit_submit(request: Request, host: str, component: str,
                          proposed: str = Form(...), action: str = Form("preview")):
        f = fleet(request)
        st = await f.poll_one(host, "read before edit")
        if not st.ok:
            return render(request, "message.html", title="Device unreachable", message=st.error)
        current = st.snapshot["config"].get(component)
        if current is None:
            raise HTTPException(404, "unknown component")

        def again(error=None, diff=None):
            return render(request, "edit.html", st=st, component=component,
                          proposed=proposed, diff=diff, error=error)

        try:
            new = json.loads(proposed)
        except json.JSONDecodeError as e:
            return again(error=f"Not valid JSON: {e}")
        if not isinstance(new, dict):
            return again(error="The configuration must be a JSON object.")
        removed = ops.removed_keys(current, new)
        if removed:
            return again(error=f"Keys cannot be removed with SetConfig: {', '.join(removed)}")
        patch = ops.changed_fields(current, new)
        if not patch:
            return again(error="No changes.")
        if action != "apply":
            return again(diff=ops.pretty_diff(current, new, component))

        method, cid = ops.setconfig_method(component, st.methods)
        try:
            result = await ops.apply_setconfig(f.entry(host), method, cid, patch)
        except (ShellyError, ShellyUnreachable) as e:
            return again(error=f"Device rejected the change: {e}")
        log.info("%s %s applied on %s: %s", method, component, st.name, sorted(patch))
        await f.poll_one(host, f"{method} {component} via UI")
        msg = f"{method} applied to {component}."
        if result.get("restart_required"):
            msg += " The device reports that a restart is required for this change to take effect."
        return render(request, "message.html", title="Change applied", message=msg,
                      back=f"/device/{host}", offer_reboot=bool(result.get("restart_required")), host=host)

    return app
