from __future__ import annotations

import os
import plistlib
from pathlib import Path

SERVICE_PLIST = "Library/LaunchAgents/com.aneyman.agent-lb.plist"


def service_database_url(plist: Path | None = None) -> str | None:
    path = plist or Path.home() / SERVICE_PLIST
    try:
        with path.open("rb") as stream:
            url = plistlib.load(stream).get("EnvironmentVariables", {}).get("AGENT_LB_DATABASE_URL")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return url if isinstance(url, str) and url else None


def cli_database_url(explicit: str | None = None) -> str:
    from app.core.config.settings import get_settings

    url = explicit or os.environ.get("AGENT_LB_DATABASE_URL")
    if url:
        return url
    url = get_settings().database_url
    if url.startswith("sqlite"):
        return service_database_url() or url
    return url
