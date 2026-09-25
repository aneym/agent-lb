from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent
# common.py -> open_factory/ -> open-factory/ -> clients/
REPO_CLIENTS = PKG.parents[1]
LOCAL_BIN = Path.home() / ".local" / "bin"


def resolve_bin(name: str) -> str | None:
    """Prefer this checkout's clients, then ~/.local/bin, then PATH (never zsh function wrappers)."""
    repo = REPO_CLIENTS / name
    if repo.is_file() and os.access(repo, os.X_OK):
        return str(repo)
    local = LOCAL_BIN / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which(name)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
