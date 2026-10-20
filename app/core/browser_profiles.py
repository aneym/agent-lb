from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CHROME_ROOT = Path.home() / "Library/Application Support/agent-lb-chrome"
CDP_PORT_BASE = 9333


@dataclass(frozen=True)
class BrowserProfile:
    email: str
    dir: str
    providers: tuple[str, ...]
    cdp_port: int


def registry_path() -> Path:
    return Path(os.getenv("AGENT_LB_STATE_DIR", str(Path.home() / ".agent-lb/state"))) / "browser-profiles.json"


def load_profiles() -> dict[str, BrowserProfile]:
    try:
        data = json.loads(registry_path().read_text())
        if data["version"] != 1 or not isinstance(data["profiles"], dict):
            return {}
        profiles = {}
        ports: set[int] = set()
        for email, row in data["profiles"].items():
            port = row["cdpPort"]
            if (
                not isinstance(email, str)
                or not email
                or email != email.lower()
                or not isinstance(row["dir"], str)
                or not row["dir"]
                or not isinstance(row["providers"], list)
                or any(provider not in {"anthropic", "openai"} for provider in row["providers"])
                or type(port) is not int
                or not CDP_PORT_BASE <= port <= 65535
                or port in ports
            ):
                return {}
            ports.add(port)
            profiles[email] = BrowserProfile(email, row["dir"], tuple(row["providers"]), port)
        return profiles
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return {}


def save_profiles(profiles: dict[str, BrowserProfile]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "profiles": {
            email: {"dir": profile.dir, "providers": profile.providers, "cdpPort": profile.cdp_port}
            for email, profile in profiles.items()
        },
    }
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".browser-profiles-")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def add_profile(email: str, *, dir: str | None, providers: Sequence[str]) -> BrowserProfile:
    email = email.strip().lower()
    if not email or any(provider not in {"anthropic", "openai"} for provider in providers):
        raise ValueError("email and supported providers are required")
    profiles = load_profiles()
    previous = profiles.get(email)
    port = previous.cdp_port if previous else CDP_PORT_BASE
    used = {profile.cdp_port for key, profile in profiles.items() if key != email}
    while port in used:
        port += 1
    if port > 65535:
        raise ValueError("no CDP ports available")
    directory = dir if dir is not None else email
    if not directory or (not Path(directory).is_absolute() and ".." in Path(directory).parts):
        raise ValueError("profile dir must be absolute or relative within the Chrome root")
    merged = tuple(dict.fromkeys((previous.providers if previous else ()) + tuple(providers)))
    profile = BrowserProfile(email, directory, merged, port)
    profiles[email] = profile
    save_profiles(profiles)
    return profile


def profile_for(email: str) -> BrowserProfile | None:
    return load_profiles().get(email.strip().lower())


def user_data_dir(profile: BrowserProfile) -> Path:
    return Path(os.getenv("AGENT_LB_CHROME_ROOT", str(CHROME_ROOT))) / profile.dir
