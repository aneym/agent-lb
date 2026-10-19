from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


def ensure_harness_mcp(host_home, run):
    home = Path(host_home).expanduser().resolve()
    paths = {
        "codex": ("codex", home / ".codex/config.toml"),
        "cursor": ("cursor-agent", home / ".cursor/mcp.json"),
        "devin": ("devin", home / ".config/devin/mcp_config.json"),
        "pi": ("pi", home / ".pi/agent/mcp.json"),
    }
    installed = {}
    search_path = os.pathsep.join((str(home / ".local/bin"), os.environ.get("PATH", "")))
    for name, (binary, path) in paths.items():
        binaries = (binary, "cursor", "agent") if name == "cursor" else (binary,)
        installed[name] = any(shutil.which(item, path=search_path) for item in binaries)
    result = {"claude": "absent", **{name: "absent" for name in paths}}
    source = home / ".claude.json"

    def read_servers(path):
        try:
            content = path.read_text()
            config = tomllib.loads(content) if path.suffix == ".toml" else json.loads(content)
        except (OSError, ValueError):
            return {}
        if not isinstance(config, dict):
            return {}
        entries = config.get("mcpServers", config.get("mcp_servers", config))
        return entries if isinstance(entries, dict) else {}

    servers = read_servers(source)
    plugin_roots = [home / ".agent-rails/installed-runtime/agent-plugin-kit",
                    home / ".agent-rails/tools/agent-plugin-kit"]
    plugins = home / ".claude/plugins"
    registry = plugins / "installed_plugins.json"
    if registry.is_file():
        records = json.loads(registry.read_text()).get("plugins", {})
        for name, installs in records.items():
            if name.split("@")[0] in ("agent", "agent-plugin-kit"):
                for install in installs:
                    if install.get("installPath"):
                        plugin_roots.append(Path(install["installPath"]).expanduser())
    for pattern in ("cache/*/agent-plugin-kit/*", "marketplaces/agent-plugin-kit",
                    "marketplaces/*/plugins/agent-plugin-kit", "marketplaces/*/plugins/agent"):
        plugin_roots.extend(sorted(plugins.glob(pattern)))
    plugin_sources = []
    for root in dict.fromkeys(plugin_roots):
        for pattern in ("**/.mcp.json", "mcp.json"):
            for path in sorted(root.glob(pattern)):
                entries = read_servers(path)
                entries = json.loads(json.dumps(entries).replace("${CLAUDE_PLUGIN_ROOT}", json.dumps(str(path.parent))[1:-1]))
                plugin_sources.append(entries)
    fallbacks = []
    for harness, (_, path) in paths.items():
        if not installed[harness]:
            continue
        entries = read_servers(path)
        if harness == "codex":
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                if "env_vars" in entry:
                    entry["env"] = {key: "${" + key + "}" for key in entry["env_vars"]}
                if "env_http_headers" in entry:
                    entry["headers"] = {key: "${" + value + "}" for key, value in entry["env_http_headers"].items()}
        elif harness == "cursor":
            entries = json.loads(re.sub(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", r"${\1}", json.dumps(entries)))
        fallbacks.append(entries)
    for pattern in ("**/*.json", "**/*.toml"):
        fallbacks.extend(read_servers(path) for path in sorted(Path(__file__).parent.glob(pattern)))
    for name in ("agent", "unblock", "exa"):
        for entries in [*plugin_sources, *(fallbacks if name != "agent" else [])]:
            if name not in servers and name in entries:
                servers[name] = entries[name]
    missing = [name for name in ("agent", "unblock", "exa") if name not in servers]
    if missing:
        result["_unresolved"] = missing
    reference = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    desired = {}
    for name in ("agent", "unblock", "exa"):
        if name not in servers:
            continue
        entry = servers[name]
        if not isinstance(entry, dict):
            raise ValueError(f"invalid MCP source entry: {name}")
        transport = entry.get("type", "stdio" if "command" in entry else "http")
        if transport not in ("stdio", "http"):
            raise ValueError(f"unsupported MCP transport: {name}")
        wanted = {key: entry[key] for key in ("command", "args", "env") if key in entry} if transport == "stdio" else {
            key: entry[key] for key in ("url", "headers") if key in entry
        }
        endpoint = wanted.get("command") if transport == "stdio" else wanted.get("url")
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError(f"missing MCP endpoint: {name}")
        if transport == "http":
            url = urlsplit(endpoint)
            if url.username or url.password or url.query or url.fragment:
                raise ValueError(f"MCP URL must not contain credentials or query values: {name}")
        if any(not isinstance(value, str) or not reference.fullmatch(value)
               for key in ("env", "headers") for value in wanted.get(key, {}).values()):
            raise ValueError(f"MCP credentials must be environment references: {name}")
        if not isinstance(wanted.get("args", []), list) or any(not isinstance(value, str) for value in wanted.get("args", [])):
            raise ValueError(f"invalid MCP arguments: {name}")
        args = wanted.get("args", [])
        if any(re.search(r"(?i)(?:token|api[_-]?key|password|secret)[=:]", value) and not reference.search(value)
               for value in [endpoint, *args]):
            raise ValueError(f"inline MCP credential refused: {name}")
        desired[name] = wanted
    if source.is_file() or shutil.which("claude", path=search_path):
        result["claude"] = "ok"
    changes = []
    for harness, (_, path) in paths.items():
        if not installed[harness]:
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(home):
            raise ValueError(f"MCP config must be a regular file inside host home: {harness}")
        before = path.read_text() if path.exists() else ""
        if harness == "codex":
            config = tomllib.loads(before)
            translated = {}
            for name, entry in desired.items():
                item = {key: value for key, value in entry.items() if key in ("command", "args", "url")}
                if "env" in entry:
                    if any(reference.fullmatch(value)[1] != key for key, value in entry["env"].items()):
                        raise ValueError(f"Codex env aliases unsupported: {name}")
                    item["env_vars"] = list(entry["env"])
                if "headers" in entry:
                    item["env_http_headers"] = {key: reference.fullmatch(value)[1] for key, value in entry["headers"].items()}
                translated[name] = item
            current = config.get("mcp_servers", {})
            if all(current.get(name) == entry for name, entry in translated.items()):
                result[harness] = "ok"
                continue
            headers = list(re.finditer(r"(?m)^\s*\[(?!\[)([^\]\n]+)\][ \t]*(?:#[^\n]*)?$", before))
            spans = []
            for index, header in enumerate(headers):
                parsed = tomllib.loads(header.group() + "\n")
                owned = parsed.get("mcp_servers", {})
                if any(name in owned for name in desired):
                    spans.append((header.start(), headers[index + 1].start() if index + 1 < len(headers) else len(before)))
            after = before
            for start, end in reversed(spans):
                after = after[:start] + after[end:]
            for name, entry in translated.items():
                after = after.rstrip() + f'\n\n[mcp_servers.{name}]\n'
                for key, value in entry.items():
                    encoded = ("{ " + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items()) + " }") if isinstance(value, dict) else json.dumps(value)
                    after += f"{key} = {encoded}\n"
            parsed = tomllib.loads(after)
            if any(parsed["mcp_servers"][name] != entry for name, entry in translated.items()):
                raise ValueError("Codex MCP serialization mismatch")
        else:
            config = json.loads(before or "{}")
            current = config.setdefault("mcpServers", {})
            translated = json.loads(json.dumps(desired))
            if harness == "cursor":
                for entry in translated.values():
                    for key in ("env", "headers"):
                        if key in entry:
                            entry[key] = {k: reference.sub(r"${env:\1}", v) for k, v in entry[key].items()}
            if all(current.get(name) == entry for name, entry in translated.items()):
                result[harness] = "ok"
                continue
            current.update(translated)
            after = json.dumps(config, indent=2) + "\n"
        changes.append((harness, path, before, after))
    for harness, path, before, after in changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        if (path.read_text() if path.exists() else "") != before:
            raise ValueError(f"MCP config changed during install: {harness}")
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(after)
            os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        result[harness] = "fixed"
    return result


if __name__ == "__main__":
    print(json.dumps(ensure_harness_mcp(Path.home(), subprocess.run), sort_keys=True))
