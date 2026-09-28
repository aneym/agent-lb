from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path

from fastapi import HTTPException, Request

from app.core.config.settings import get_settings
from app.core.request_locality import resolve_request_client_host

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushSource:
    name: str
    tailscale_logins: tuple[str, ...]
    max_accounts: int = 25


_sources_cache: dict[Path, tuple[int, list[PushSource]]] = {}
_login_cache: dict[str, tuple[float, str]] = {}


def load_push_sources(path: Path) -> list[PushSource]:
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        _sources_cache.pop(path, None)
        return []
    except OSError:
        logger.warning("Cannot read federation push sources")
        _sources_cache.pop(path, None)
        return []
    cached = _sources_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        payload = json.loads(path.read_text())
        entries = payload["sources"]
        if not isinstance(entries, list):
            raise ValueError("sources must be a list")
        sources = []
        for entry in entries:
            name, logins = entry["name"], entry["tailscale_logins"]
            maximum = entry.get("max_accounts", 25)
            if (
                not isinstance(name, str) or not name.strip()
                or not isinstance(logins, list) or not logins
                or any(not isinstance(login, str) or not login.strip() for login in logins)
                or type(maximum) is not int or maximum < 0
            ):
                raise ValueError("invalid source")
            sources.append(PushSource(name, tuple(logins), maximum))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        logger.warning("Malformed federation push sources; disabling push receiver")
        sources = []
    _sources_cache[path] = (mtime, sources)
    return sources


async def resolve_tailscale_login(ip: str) -> str | None:
    cached = _login_cache.get(ip)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    settings = get_settings()
    binary = settings.federation_tailscale_bin or next(
        (str(path) for path in (
            Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
            Path("/usr/local/bin/tailscale"),
            Path("/opt/homebrew/bin/tailscale"),
        ) if path.is_file()),
        "tailscale",
    )
    try:
        process = await asyncio.create_subprocess_exec(
            binary, "whois", "--json", ip,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return None
        if process.returncode != 0:
            return None
        output = stdout.decode("utf-8")
        profile = json.loads(output[output.index("{"):])["UserProfile"]
        login = profile["LoginName"]
        if not isinstance(login, str) or not login:
            return None
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return None
    _login_cache[ip] = (time.monotonic() + 60, login)
    return login


def match_source(login: str | None, sources: list[PushSource]) -> PushSource | None:
    if login is None:
        return None
    return next((source for source in sources if login.casefold() in (
        entry.casefold() for entry in source.tailscale_logins
    )), None)


async def require_federation_push_source(request: Request) -> PushSource:
    ip = resolve_request_client_host(request)
    try:
        if ip is None or ip_address(ip).is_loopback:
            raise ValueError("No remote client IP")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Federation push source not allowed") from exc
    sources = load_push_sources(get_settings().federation_push_sources_path)
    if not sources:
        raise HTTPException(status_code=403, detail="Federation push source not allowed")
    source = match_source(await resolve_tailscale_login(ip), sources)
    if source is None:
        raise HTTPException(status_code=403, detail="Federation push source not allowed")
    return source
