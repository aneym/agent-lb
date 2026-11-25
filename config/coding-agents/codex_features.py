from __future__ import annotations

import re
import tomllib

WANTED = {"code_mode": True}
TOP = {"suppress_unstable_features_warning": True}
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


def set_top(text: str, name: str, value: bool) -> str:
    lines = text.splitlines(keepends=True)
    setting = f"{name} = {'true' if value else 'false'}\n"
    key = re.compile(rf"^\s*{re.escape(name)}\s*=")
    end = next((i for i, line in enumerate(lines) if TABLE.match(line)), len(lines))
    for i in range(end):
        if key.match(lines[i]):
            lines[i] = setting
            return "".join(lines)
    if end == len(lines) and lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.insert(end, setting)
    return "".join(lines)


def feature_changes(config_text: str, install: bool) -> str:
    if not install:
        return config_text
    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return config_text
    text = config_text
    for name, value in TOP.items():
        if config.get(name) is not value:
            text = set_top(text, name, value)
    for name, value in WANTED.items():
        if config.get("features", {}).get(name) is not value:
            text = set_feature(text, name, value)
    try:
        result = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        result = {}
    if any(result.get(name) is not value for name, value in TOP.items()) or any(
        result.get("features", {}).get(name) is not value for name, value in WANTED.items()
    ):
        print("SKIP Codex features: config.toml layout is not editable")
        return config_text
    return text
