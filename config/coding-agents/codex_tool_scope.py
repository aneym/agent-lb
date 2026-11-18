from __future__ import annotations

import re
import socket
import sys
import tomllib
from pathlib import Path

STUDIO = "alexs-mac-studio"
UNUSED_SERVERS = {
    "ax42": ("spacesheep", "railway", "content_app_map_probe"),
    "forge": ("doop", "doop-rails"),
    "ax102": ("doop", "doop-content-one"),
    STUDIO: ("computer-history", "firecrawl", "logo-dev", "doop"),
}
APP_ALLOWLIST = {
    STUDIO: (
        "connector_76869538009648d5b282a4bb21c3d157",
        "connector_2128aebfecb84f64a069897515042a44",
    ),
}
HEADER = re.compile(r"^\s*\[(?!\[)([^\[\]]+)\]\s*(#.*)?$")
ENABLED = re.compile(r"^\s*enabled\s*=")


def table_name(header: str) -> str:
    return header.replace('"', "").replace(" ", "")


def set_enabled(text: str, table: str, value: bool) -> str:
    lines = text.splitlines(keepends=True)
    setting = f"enabled = {'true' if value else 'false'}\n"
    start = next(
        (i for i, line in enumerate(lines) if (m := HEADER.match(line)) and table_name(m.group(1)) == table),
        None,
    )
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines) + f"\n[{table}]\n{setting}"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
        len(lines),
    )
    for i in range(start + 1, end):
        if ENABLED.match(lines[i]):
            lines[i] = setting
            return "".join(lines)
    lines.insert(start + 1, setting)
    return "".join(lines)


def enabled_value(config: dict, group: str, name: str) -> object:
    entry = config.get(group, {}).get(name)
    return entry.get("enabled") if isinstance(entry, dict) else None


def trimmed_servers(config: dict) -> list[str]:
    host = socket.gethostname().split(".")[0].lower()
    servers = config.get("mcp_servers", {})
    return [
        name
        for name in UNUSED_SERVERS.get(host, ())
        if name in servers and enabled_value(config, "mcp_servers", name) is not True
    ]


def scope_note(home: Path) -> str:
    try:
        config = tomllib.loads((home / ".codex" / "config.toml").read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return ""
    names = trimmed_servers(config)
    if not names:
        return ""
    command = f"python3 {Path(__file__).resolve()} enable <server>"
    return (
        f"This host turns off MCP servers Codex rarely uses: {', '.join(names)}. "
        f"When a task needs a tool from one of them, run `{command}`; the server loads on the next Codex run "
        "and stays on. Say in your report when this run had to go without it."
    )


def tool_scope_changes(config_text: str, install: bool) -> str:
    if not install:
        return config_text
    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return config_text
    host = socket.gethostname().split(".")[0].lower()
    wanted = {("mcp_servers", name): False for name in trimmed_servers(config)}
    if host in APP_ALLOWLIST:
        wanted[("apps", "_default")] = False
        wanted.update({("apps", app): True for app in APP_ALLOWLIST[host]})
    text = config_text
    for (group, name), value in wanted.items():
        if enabled_value(config, group, name) is not value:
            text = set_enabled(text, f"{group}.{name}", value)
    try:
        result = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        result = {}
    if any(enabled_value(result, group, name) is not value for (group, name), value in wanted.items()):
        print("SKIP Codex tool scope: config.toml layout is not editable")
        return config_text
    return text


def enable(name: str) -> int:
    path = Path.home() / ".codex" / "config.toml"
    text = path.read_text()
    servers = tomllib.loads(text).get("mcp_servers", {})
    if name not in servers:
        print(f"no MCP server {name} in {path}; known: {', '.join(sorted(servers))}", file=sys.stderr)
        return 1
    updated = set_enabled(text, f"mcp_servers.{name}", True)
    if enabled_value(tomllib.loads(updated), "mcp_servers", name) is not True:
        print(f"could not set enabled = true for {name} in {path}", file=sys.stderr)
        return 1
    path.write_text(updated)
    print(f"{name} is on in {path}; it loads on the next Codex run and the sync keeps it on")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "enable":
        sys.exit("usage: codex_tool_scope.py enable <server>")
    sys.exit(enable(sys.argv[2]))
