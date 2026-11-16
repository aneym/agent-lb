from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

PALETTE: dict[str, int] = {
    "Green": 0xFF00FF00,
    "Blue": 0xFF1A73E8,
    "Red": 0xFFD93025,
    "Purple": 0xFF9334E6,
    "Orange": 0xFFF57C00,
    "Teal": 0xFF00897B,
    "Pink": 0xFFE52592,
    "Yellow": 0xFFFBBC04,
    "Indigo": 0xFF3F51B5,
    "Brown": 0xFF795548,
    "Cyan": 0xFF00ACC1,
    "Lime": 0xFF7CB342,
    "Gray": 0xFF5F6368,
}
TONAL_SPOT = 1
PROVIDER_NAMES = {"anthropic": "Claude", "openai": "ChatGPT"}


def pick_color(current: str | None, used: Iterable[str | None]) -> str:
    if current in PALETTE:
        return current
    taken = [color for color in used if color in PALETTE]
    return min(PALETTE, key=lambda color: (taken.count(color), list(PALETTE).index(color)))


def theme_color(user_data: Path) -> str | None:
    try:
        name = json.loads((user_data / "Local State").read_text())["profile"]["info_cache"]["Default"]["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    color = name.split(" · ", 1)[0] if isinstance(name, str) else None
    return color if color in PALETTE else None


def display_name(color: str, label: str, providers: Sequence[str]) -> str:
    provider = " + ".join(PROVIDER_NAMES.get(provider, provider) for provider in providers) or "Chrome"
    return f"{color} · {label} · {provider}"


def _signed(argb: int) -> int:
    return argb - (1 << 32) if argb >= 1 << 31 else argb


def _rewrite(path: Path, update) -> None:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    update(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-theme-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, separators=(",", ":"), ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_theme(user_data: Path, color: str, name: str) -> None:
    seed = _signed(PALETTE[color])

    def preferences(data: dict) -> None:
        theme = data.setdefault("browser", {}).setdefault("theme", {})
        theme["user_color2"] = seed
        theme["color_variant2"] = TONAL_SPOT
        data.setdefault("profile", {})["name"] = name

    def local_state(data: dict) -> None:
        cache = data.setdefault("profile", {}).setdefault("info_cache", {}).setdefault("Default", {})
        cache.update(name=name, is_using_default_name=False, profile_color_seed=seed)

    _rewrite(user_data / "Default" / "Preferences", preferences)
    _rewrite(user_data / "Local State", local_state)
