from __future__ import annotations

import re
import tomllib

WANTED = {"code_mode": True}
HEADER = re.compile(r"^\s*\[(?!\[)\s*features\s*\]\s*(#.*)?$")
TABLE = re.compile(r"^\s*\[")


def set_feature(text: str, name: str, value: bool) -> str:
    lines = text.splitlines(keepends=True)
    setting = f"{name} = {'true' if value else 'false'}\n"
    key = re.compile(rf"^\s*{re.escape(name)}\s*=")
    start = next((i for i, line in enumerate(lines) if HEADER.match(line)), None)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines) + f"\n[features]\n{setting}"
    end = next((i for i in range(start + 1, len(lines)) if TABLE.match(lines[i])), len(lines))
    for i in range(start + 1, end):
        if key.match(lines[i]):
            lines[i] = setting
            return "".join(lines)
    lines.insert(start + 1, setting)
    return "".join(lines)


def feature_changes(config_text: str, install: bool) -> str:
    if not install:
        return config_text
    try:
        features = tomllib.loads(config_text).get("features", {})
    except tomllib.TOMLDecodeError:
        return config_text
    text = config_text
    for name, value in WANTED.items():
        if features.get(name) is not value:
            text = set_feature(text, name, value)
    try:
        result = tomllib.loads(text).get("features", {})
    except tomllib.TOMLDecodeError:
        result = {}
    if any(result.get(name) is not value for name, value in WANTED.items()):
        print("SKIP Codex features: config.toml layout is not editable")
        return config_text
    return text
