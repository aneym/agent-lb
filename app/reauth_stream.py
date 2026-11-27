from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import shutil
import socket
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.requests import HTTPConnection
from websockets.exceptions import WebSocketException

from app.core.auth.dashboard_mode import DashboardAuthMode, get_dashboard_request_auth
from app.core.auth.dependencies import validate_dashboard_session
from app.core.browser_profiles import profile_for
from app.core.cdp_page import CdpError
from app.core.clients.oauth import OAuthError
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import AppError
from app.core.profile_launch import (
    LaunchError,
    isolated_browser,
    isolated_page,
    managed_process,
    registered_profile_dir,
)
from app.core.profile_lock import profile_held
from app.core.request_locality import INGRESS_PROOF_SCOPE_KEY, resolve_request_client_host
from app.db.session import get_background_session
from app.lb_service import shown_code
from app.modules.accounts.repository import AccountsRepository
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store
from app.modules.oauth.schemas import OauthStartRequest
from app.modules.shared.schemas import DashboardModel
from app.reauth_enroll import refusal

router = APIRouter(prefix="/api/reauth/browser", tags=["dashboard"])
_PREFIX = "/api/reauth/browser"
_TTL = 300
_MAX_SESSIONS = 4
_MAX_MESSAGE = 65536
_PACKAGE_ROOT = Path.home() / ".local/share/agent-lb-browser/current/usr"


class BrowserStart(DashboardModel):
    account_id: str = Field(min_length=1, max_length=200)


class BrowserStarted(DashboardModel):
    session_id: str
    flow_id: str
    websocket_path: str


@dataclass
class BrowserSession:
    owner: bytes
    account_id: str
    email: str
    directory: Path
    flow: dict[str, str]
    expires: float = field(default_factory=lambda: time.monotonic() + _TTL)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    connected: bool = False


def _sessions(connection: HTTPConnection) -> dict[str, BrowserSession]:
    state = connection.app.state
    sessions = getattr(state, "reauth_browser_sessions", None)
    if sessions is None:
        sessions = state.reauth_browser_sessions = {}
    return sessions


def _require_origin(connection: HTTPConnection) -> None:
    origin = os.getenv("AGENT_LB_REAUTH_ORIGIN", "")
    try:
        parsed = urlsplit(origin)
    except ValueError:
        raise HTTPException(503, "Browser reauth origin is not configured") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or origin != f"https://{parsed.netloc}"
    ):
        raise HTTPException(503, "Browser reauth origin is not configured")
    if connection.headers.getlist("origin") != [origin] or connection.headers.getlist("host") != [parsed.netloc]:
        raise HTTPException(403, "Browser reauth requires the exact dashboard origin")


async def _principal(connection: HTTPConnection) -> bytes:
    configured = os.getenv("AGENT_LB_REAUTH_TRUSTED_CLIENT_IPS", "").strip()
    if configured:
        try:
            allowed = {ip_address(address.strip()) for address in configured.split(",")}
            if any(address.is_loopback or address.is_unspecified for address in allowed):
                raise ValueError
        except ValueError:
            raise HTTPException(503, "Browser reauth trusted client IPs are invalid") from None
        if connection.scope.get(INGRESS_PROOF_SCOPE_KEY) is not True:
            raise HTTPException(403, "Browser reauth requires verified dashboard ingress")
        try:
            client = ip_address(resolve_request_client_host(connection) or "")
        except ValueError:
            raise HTTPException(403, "Browser reauth requires a trusted client") from None
        if client not in allowed:
            raise HTTPException(403, "Browser reauth requires a trusted client")
        return hashlib.sha256(f"ingress:{client}".encode()).digest()
    if get_settings().dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise HTTPException(403, "Browser reauth requires an authenticated dashboard mode")
    await validate_dashboard_session(connection)
    proxy_auth = get_dashboard_request_auth(connection)
    if proxy_auth is not None and proxy_auth.mode == DashboardAuthMode.TRUSTED_HEADER and proxy_auth.actor:
        return hashlib.sha256(f"proxy:{proxy_auth.actor}".encode()).digest()
    cookie = connection.cookies.get(DASHBOARD_SESSION_COOKIE)
    state = get_dashboard_session_store().get(cookie)
    if cookie and state is not None and state.password_verified and (await get_settings_cache().get()).password_hash:
        return hashlib.sha256(f"session:{cookie}".encode()).digest()
    raise HTTPException(401, "Sign into the dashboard before opening a browser session")


def _x11vnc() -> tuple[str, dict[str, str]]:
    packaged = _PACKAGE_ROOT / "bin/x11vnc"
    executable = os.getenv("AGENT_LB_X11VNC") or (str(packaged) if packaged.is_file() else shutil.which("x11vnc"))
    if not executable or not os.access(executable, os.X_OK):
        raise HTTPException(503, "x11vnc is not installed")
    libraries = os.getenv("AGENT_LB_X11VNC_LIBRARY_PATH")
    if not libraries and executable == str(packaged):
        libraries = str(_PACKAGE_ROOT / "lib/x86_64-linux-gnu")
    return executable, {"LD_LIBRARY_PATH": libraries} if libraries else {}


async def _profile(account_id: str) -> tuple[str, Path]:
    async with get_background_session() as db:
        account = await AccountsRepository(db).get_by_id(account_id)
        if account is None:
            raise HTTPException(404, "Account not found")
        if refusal({"accountId": account.id, "provider": account.provider, "ownerInstance": account.owner_instance}):
            raise HTTPException(403, "This account cannot use browser reauth on this instance")
        email = account.email.strip().lower()
    profile = profile_for(email)
    if profile is None:
        raise HTTPException(409, "No registered browser profile for this account")
    try:
        return email, registered_profile_dir(profile, "anthropic")
    except LaunchError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/sessions", response_model=BrowserStarted, status_code=201)
async def create_session(request: Request, payload: BrowserStart, response: Response) -> BrowserStarted:
    from app.dependencies import get_oauth_context

    _require_origin(request)
    owner = await _principal(request)
    if get_settings().federation_peer_url or not sys.platform.startswith("linux"):
        raise HTTPException(409, "Browser reauth must run on the Linux account owner")
    email, directory = await _profile(payload.account_id)
    _x11vnc()
    if not shutil.which("Xvfb"):
        raise HTTPException(503, "Xvfb is not installed")
    sessions = _sessions(request)
    for session_id, session in list(sessions.items()):
        if time.monotonic() >= session.expires:
            session.stop.set()
            if not session.connected:
                del sessions[session_id]
    if len(sessions) >= _MAX_SESSIONS or any(session.directory == directory for session in sessions.values()):
        raise HTTPException(409, "A browser session is busy; close it or wait for it to expire")
    session_id = secrets.token_urlsafe(24)
    session = BrowserSession(owner=owner, account_id=payload.account_id, email=email, directory=directory, flow={})
    sessions[session_id] = session
    try:
        async with get_background_session() as db:
            flow = await get_oauth_context(db).service.start_oauth(
                OauthStartRequest(provider="anthropic", force_method="browser", expected_account_id=payload.account_id)
            )
        if (
            flow.method != "browser"
            or flow.expected_account_id != payload.account_id
            or not flow.flow_id
            or not flow.authorization_url
            or not flow.callback_url
        ):
            raise HTTPException(502, "OAuth did not return an account-bound browser flow")
        session.flow = {
            "flowId": flow.flow_id,
            "authorizationUrl": flow.authorization_url,
            "callbackUrl": flow.callback_url,
        }
    except OAuthError:
        sessions.pop(session_id, None)
        raise HTTPException(502, "Could not start browser OAuth") from None
    except BaseException:
        sessions.pop(session_id, None)
        raise
    response.headers["Cache-Control"] = "no-store"
    return BrowserStarted(
        session_id=session_id, flow_id=flow.flow_id, websocket_path=f"{_PREFIX}/sessions/{session_id}/ws"
    )


def _owned(connection: HTTPConnection, session_id: str, owner: bytes) -> BrowserSession:
    session = _sessions(connection).get(session_id)
    if session is None or not session.flow or not hmac.compare_digest(session.owner, owner):
        raise HTTPException(404, "Browser session not found")
    return session


@router.delete("/sessions/{session_id}", status_code=204)
async def stop_session(request: Request, session_id: str) -> Response:
    _require_origin(request)
    session = _owned(request, session_id, await _principal(request))
    session.stop.set()
    if not session.connected:
        _sessions(request).pop(session_id, None)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/novnc/{asset:path}", include_in_schema=False)
async def novnc_asset(request: Request, asset: str) -> FileResponse:
    await _principal(request)
    root = Path(os.getenv("AGENT_LB_NOVNC_ROOT", str(_PACKAGE_ROOT / "share/novnc"))).resolve()
    paths = Path(asset).parts
    if not paths or paths[0] not in {"core", "vendor"}:
        raise HTTPException(404)
    path = (root / asset).resolve()
    if not path.is_relative_to(root) or path.suffix != ".js" or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": "private, no-store"})


def _open_display(stack: ExitStack, session: BrowserSession) -> tuple[socket.socket, int]:
    if not stack.enter_context(profile_held(session.email)):
        raise LaunchError("profile is busy")
    profile = profile_for(session.email)
    if profile is None:
        raise LaunchError("registered browser profile was removed")
    browser = stack.enter_context(isolated_browser(profile, "anthropic", expected_directory=session.directory))
    page = isolated_page(browser, session.flow["authorizationUrl"])
    page.detach()
    executable, libraries = _x11vnc()
    server, child = socket.socketpair()
    stack.callback(server.close)
    with child:
        stack.enter_context(
            managed_process(
                [
                    executable,
                    "-norc",
                    "-inetd",
                    "-q",
                    "-nopw",
                    "-noremote",
                    "-rfbport",
                    "0",
                    "-noclipboard",
                    "-nosetclipboard",
                    "-noprimary",
                    "-nosetprimary",
                ],
                env={**browser.env, **libraries},
                stdin=child,
                stdout=child,
            )
        )
    server.setblocking(False)
    return server, browser.port


async def _handle_callback(ws: WebSocket, session: BrowserSession, port: int) -> None:
    from app.dependencies import get_oauth_context

    while True:
        await asyncio.sleep(1)
        if not hmac.compare_digest(session.owner, await _principal(ws)):
            return
        async with get_background_session() as db:
            status = await get_oauth_context(db).service.oauth_status(flow_id=session.flow["flowId"])
            if status.status in {"success", "error"}:
                return
        try:
            code = await asyncio.to_thread(shown_code, port, session.flow)
        except (CdpError, WebSocketException, OSError, TimeoutError):
            continue
        if code:
            email, directory = await _profile(session.account_id)
            if email != session.email or directory != session.directory or session.stop.is_set():
                return
            if not hmac.compare_digest(session.owner, await _principal(ws)):
                return
            async with get_background_session() as db:
                await get_oauth_context(db).service.manual_callback(code, flow_id=session.flow["flowId"])
            return


async def _relay(ws: WebSocket, server: socket.socket, session: BrowserSession, port: int) -> None:
    loop = asyncio.get_running_loop()

    async def viewer_to_vnc() -> None:
        while True:
            frame = await ws.receive()
            if frame["type"] == "websocket.disconnect":
                return
            data = frame.get("bytes")
            if data is None or len(data) > _MAX_MESSAGE:
                return
            await loop.sock_sendall(server, data)

    async def vnc_to_viewer() -> None:
        while data := await loop.sock_recv(server, _MAX_MESSAGE):
            await ws.send_bytes(data)

    tasks = [
        asyncio.create_task(work)
        for work in (viewer_to_vnc(), vnc_to_viewer(), session.stop.wait(), _handle_callback(ws, session, port))
    ]
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=max(0, session.expires - time.monotonic()), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket("/sessions/{session_id}/ws")
async def browser_socket(ws: WebSocket, session_id: str) -> None:
    try:
        _require_origin(ws)
        owner = await _principal(ws)
        session = _owned(ws, session_id, owner)
        email, directory = await _profile(session.account_id)
        if email != session.email or directory != session.directory:
            raise HTTPException(403)
        if (
            _sessions(ws).get(session_id) is not session
            or session.connected
            or session.stop.is_set()
            or time.monotonic() >= session.expires
        ):
            raise HTTPException(409)
    except (HTTPException, AppError):
        await ws.close(code=1008)
        return
    session.connected = True
    code = 1000
    stack = ExitStack()
    launch = None
    try:
        protocols = ws.scope.get("subprotocols", [])
        await ws.accept(subprotocol="binary" if "binary" in protocols else None)
        launch = asyncio.create_task(asyncio.to_thread(_open_display, stack, session))
        server, port = await asyncio.shield(launch)
        if session.stop.is_set() or time.monotonic() >= session.expires:
            return
        await _relay(ws, server, session, port)
    except WebSocketDisconnect:
        pass
    except (HTTPException, AppError, OAuthError, OSError, CdpError, WebSocketException, TimeoutError):
        code = 1011
    finally:
        try:
            await ws.close(code=code)
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
        try:
            if launch is not None:
                await asyncio.gather(launch, return_exceptions=True)
            await asyncio.to_thread(stack.close)
        finally:
            _sessions(ws).pop(session_id, None)
