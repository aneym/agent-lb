from __future__ import annotations

import json
import re
import tomllib
import urllib.request
from pathlib import Path

CATALOG = Path(".codex/model-catalog.json")
PROVIDER = "agent-lb"
SETTING = re.compile(r"^\s*model_catalog_json\s*=")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESERVED_TOOLS = {"literature_search_query"}


def served_catalog(config_text: str) -> str | None:
    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return None
    if config.get("model_provider") != PROVIDER:
        return None
    base = config.get("model_providers", {}).get(PROVIDER, {}).get("base_url")
    if not isinstance(base, str) or not base.startswith(("http://127.0.0.1:", "http://localhost:")):
        return None
    try:
        with OPENER.open(base.rstrip("/") + "/models", timeout=10) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models or not all(isinstance(m, dict) and m.get("slug") for m in models):
        return None
    for model in models:
        model["tool_mode"] = None
        tools = model.get("experimental_supported_tools")
        if isinstance(tools, list):
            model["experimental_supported_tools"] = [tool for tool in tools if tool not in RESERVED_TOOLS]
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def set_catalog_line(text: str, path: Path | None) -> str:
    lines = text.splitlines(keepends=True)
    top = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    head = [line for line in lines[:top] if not SETTING.match(line)]
    if path is not None:
        if head and not head[-1].endswith("\n"):
            head[-1] += "\n"
        head.append(f"model_catalog_json = {json.dumps(str(path))}\n")
    return "".join(head + lines[top:])


def catalog_changes(home: Path, config_text: str, install: bool) -> tuple[str, dict[Path, str]]:
    path = home / CATALOG
    if not install:
        return set_catalog_line(config_text, None) if f'"{path}"' in config_text else config_text, {}
    catalog = served_catalog(config_text)
    current = path.read_text() if path.is_file() else None
    if catalog is None and current is None:
        print("SKIP Codex model catalog: the agent-lb provider served none")
        return config_text, {}
    changes = {path: catalog} if catalog is not None and catalog != current else {}
    return set_catalog_line(config_text, path), changes
