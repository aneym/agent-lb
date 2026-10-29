from __future__ import annotations

import os
from pathlib import Path

LOCAL_DATABASE_URL = "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"
PRIMARY_URL_FILE = ".agent-lb/primary-database.url"


def primary_database_url(home: Path | None = None) -> str | None:
    try:
        url = ((home or Path.home()) / PRIMARY_URL_FILE).read_text().strip()
    except OSError:
        return None
    return url or None


def cli_database_url(explicit: str | None = None) -> str:
    from app.core.config.settings import get_settings

    url = explicit or os.environ.get("AGENT_LB_DATABASE_URL")
    if url:
        return url
    url = get_settings().database_url
    if url.startswith("sqlite"):
        return primary_database_url() or url
    return url
