from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime

from websockets.exceptions import WebSocketException

from app.core.browser_profiles import BrowserProfile
from app.core.cdp_page import CdpError, CdpPage, cdp_up, close_browser
from app.core.profile_lock import profile_held
from app.profiles_cli import launch_profile
from app.reauth_page import signed_in_as

VISIT_SECONDS = 60
WEEK_HOURS = 7 * 24
_CLOUDFLARE = re.compile(r"just a moment|checking your browser|verify you are human", re.I)
_PLACE = "(() => [location.hostname, location.pathname, document.title, (document.body && document.body.innerText || '').slice(0, 4000)])()"


def due(email: str, last_visit: float | None, now: float | None = None) -> bool:
    now = now or time.time()
    if last_visit is not None and now - last_visit >= 8 * 86400:
        return True
    if last_visit is not None and now - last_visit < 6.5 * 86400:
        return False
    slot = int(hashlib.sha256(email.encode()).hexdigest(), 16) % WEEK_HOURS
    moment = datetime.fromtimestamp(now, UTC)
    return moment.weekday() * 24 + moment.hour == slot


def _place(page: CdpPage) -> list:
    try:
        value = page.evaluate(_PLACE)
    except (CdpError, WebSocketException, TimeoutError):
        return []
    return value if isinstance(value, list) and len(value) == 4 else []


def _google(page: CdpPage, email: str) -> str | None:
    place = _place(page)
    if not place:
        return None
    host, _path, title, text = place
    if _CLOUDFLARE.search(f"{title}\n{text}"):
        return "cloudflare"
    if host == "accounts.google.com":
        return "lapsed:google"
    if host == "myaccount.google.com" and email in text.lower():
        return "ok"
    return None


def _claude(page: CdpPage, email: str) -> str | None:
    place = _place(page)
    if not place:
        return None
    host, path, title, text = place
    if _CLOUDFLARE.search(f"{title}\n{text}"):
        return "cloudflare"
    if host == "claude.ai" and path.startswith("/login"):
        return "lapsed:claude"
    who = signed_in_as(page) if host == "claude.ai" else None
    if who == email:
        return "ok"
    if who:
        return "lapsed:claude"
    return None


def visit(profile: BrowserProfile) -> tuple[str, str]:
    if cdp_up(profile.cdp_port):
        return "skipped", "Chrome is already open on this profile"
    with profile_held(profile.email) as free:
        if not free:
            return "skipped", "another run holds this profile"
        launch_profile(profile, "about:blank")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not cdp_up(profile.cdp_port):
            time.sleep(0.25)
        pages: list[CdpPage] = []
        try:
            checks = []
            if profile.login == "google":
                google = CdpPage(profile.cdp_port, "https://myaccount.google.com/", background=True)
                pages.append(google)
                checks.append(("google", lambda: _google(google, profile.email)))
            claude = CdpPage(profile.cdp_port, "https://claude.ai/", background=True)
            pages.append(claude)
            checks.append(("claude", lambda: _claude(claude, profile.email)))
            results: dict[str, str] = {}
            stop = time.monotonic() + VISIT_SECONDS
            while time.monotonic() < stop and len(results) < len(checks):
                for name, check in checks:
                    if name not in results and (answer := check()):
                        results[name] = answer
                time.sleep(2)
        except (CdpError, WebSocketException, OSError, TimeoutError) as exc:
            return "unknown", f"browser: {str(exc)[:120] or type(exc).__name__}"
        finally:
            for page in pages:
                page.close()
            close_browser(profile.cdp_port)
        for name, _check in checks:
            if results.get(name, "unknown") != "ok":
                return results.get(name, "unknown"), f"{name} check"
        return "ok", ", ".join(f"{name} signed in" for name, _check in checks)
