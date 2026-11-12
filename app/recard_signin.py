from __future__ import annotations

import sqlite3
import time

from app.core.browser_profiles import profile_for, user_data_dir
from app.core.cdp_page import CdpError, cdp_up
from app.core.claude_billing import signed_in_email

_CHROME_EPOCH_S = 11644473600


def _session_cookie(email: str) -> dict:
    profile = profile_for(email)
    path = user_data_dir(profile) / "Default" / "Cookies" if profile else None
    if path is None or not path.is_file():
        return {"signedIn": False, "how": "cookie", "detail": "no cookie store"}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as db:
            rows = db.execute(
                "SELECT expires_utc FROM cookies WHERE host_key LIKE '%claude.ai' AND name = 'sessionKey'"
            ).fetchall()
    except sqlite3.Error as exc:
        return {"signedIn": None, "how": "cookie", "detail": type(exc).__name__}
    now = time.time()
    live = [row for row in rows if not row[0] or row[0] / 1_000_000 - _CHROME_EPOCH_S > now]
    return {"signedIn": bool(live), "how": "cookie", "detail": None if live else "no live claude.ai session cookie"}


def signed_in_ahead(email: str) -> dict:
    profile = profile_for(email)
    if profile is None:
        return {"signedIn": False, "how": "profile", "detail": "no browser profile"}
    if not cdp_up(profile.cdp_port):
        return _session_cookie(email)
    try:
        signed = signed_in_email(profile.cdp_port)
    except (CdpError, OSError) as exc:
        return {"signedIn": None, "how": "page", "detail": str(exc)}
    return {"signedIn": signed == email, "how": "page", "detail": None if signed == email else f"signed in as {signed}"}
