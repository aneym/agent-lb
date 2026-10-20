import platform
import socket
from pathlib import Path
from typing import Mapping

PROFILES = ("local-write", "box-write", "read-only")

_MODES = ("write", "read")
_PLACES = ("local", "box")


def is_studio(env: Mapping[str, str] | None = None) -> bool:
    if platform.system() != "Darwin":
        return False
    return "studio" in socket.gethostname().split(".")[0].lower()


def select(mode: str, place: str, requested: str | None = None, env=None) -> str:
    if mode not in _MODES:
        raise ValueError(f"codex-seat: unknown mode {mode!r}; expected write or read")
    if place not in _PLACES:
        raise ValueError(f"codex-seat: unknown place {place!r}; expected local or box")
    if requested is not None and requested not in PROFILES:
        raise ValueError(f"codex-seat: unknown profile {requested!r}; expected one of {', '.join(PROFILES)}")
    if mode == "read":
        default = "read-only"
    else:
        default = "local-write" if place == "local" else "box-write"
    profile = default if requested is None else requested
    if profile != default and profile != "read-only":
        raise ValueError(
            f"codex-seat: profile {profile} widens {mode}/{place} default {default}; profiles may only narrow"
        )
    if profile == "local-write" and not is_studio(env):
        raise ValueError("codex-seat: local-write requires Studio; this host is not Studio")
    return profile


def codex_flags(profile: str, cwd: Path, env: Mapping[str, str] | None = None) -> list[str]:
    if profile == "local-write":
        return ["-s", "danger-full-access"]
    if profile == "read-only":
        return ["-s", "read-only"]
    if profile == "box-write":
        raise ValueError("box-write runs under seat-exec on the box")
    raise ValueError(f"codex-seat: unknown profile {profile!r}; expected one of {', '.join(PROFILES)}")
