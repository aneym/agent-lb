import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

BUDGET = 10.0
CAP = 8000
PROTOCOL = "2025-06-18"
HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")
PAIR = re.compile(r"^\s*(\"[^\"]*\"|'[^']*'|[A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$")


def rails_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return False
    if host.endswith(".rails.so"):
        return parts.path.rstrip("/") == "/mcp"
    return host == "rails.so" and re.fullmatch(r"/s/[A-Za-z0-9_-]+/mcp/?", parts.path) is not None


def scalar(text):
    text = text.strip()
    if text[:1] == '"':
        end = text.find('"', 1)
        return json.loads(text[: end + 1]) if end > 0 else None
    if text[:1] == "'":
        end = text.find("'", 1)
        return text[1:end] if end > 0 else None
    if text[:1] == "{":
        out = {}
        for key, value in re.findall(r"(\"[^\"]*\"|'[^']*'|[A-Za-z0-9_-]+)\s*=\s*(\"[^\"]*\"|'[^']*')", text):
            out[key.strip("\"'")] = scalar(value)
        return out
    word = text.split("#", 1)[0].strip()
    return {"true": True, "false": False}.get(word, word)


def split_key(text):
    return [part.strip().strip("\"'") for part in re.findall(r"\"[^\"]*\"|'[^']*'|[^.]+", text.strip())]


def read_toml(path):
    try:
        text = path.read_text()
    except (OSError, UnicodeError):
        return {}
    try:
        import tomllib

        return tomllib.loads(text)
    except ImportError:
        pass
    except ValueError:
        return {}
    data, table = {}, {}
    data_root = data
    for line in text.splitlines():
        head = HEADER.match(line)
        if head:
            table = data_root
            for part in split_key(head[1]):
                table = table.setdefault(part, {}) if isinstance(table.get(part, {}), dict) else {}
            continue
        pair = PAIR.match(line)
        if pair and isinstance(table, dict):
            table[pair[1].strip("\"'")] = scalar(pair[2])
    return data


def servers(home):
    config = read_toml(home / "config.toml")
    found = {}
    for name, entry in (config.get("mcp_servers") or {}).items():
        if isinstance(entry, dict) and entry.get("enabled", True) is not False:
            found[name] = entry
    for key, entry in (config.get("plugins") or {}).items():
        if not (isinstance(entry, dict) and entry.get("enabled") is True and "@" in key):
            continue
        plugin, market = key.split("@", 1)
        base = home / "plugins" / "cache" / market / plugin
        manifests = sorted(base.glob("*/mcp.json"), key=lambda p: p.stat().st_mtime) if base.is_dir() else []
        if manifests:
            try:
                declared = json.loads(manifests[-1].read_text()).get("mcpServers") or {}
            except (OSError, ValueError):
                declared = {}
            for name, value in declared.items():
                if isinstance(value, dict):
                    found.setdefault(name, value)
    return {name: entry for name, entry in found.items() if rails_url(str(entry.get("url") or ""))}


def headers_for(entry):
    headers = {}
    for key, value in (entry.get("http_headers") or entry.get("headers") or {}).items():
        if isinstance(value, str):
            headers[key] = value
    for key, var in (entry.get("env_http_headers") or {}).items():
        if isinstance(var, str) and os.environ.get(var):
            headers[key] = os.environ[var]
    var = entry.get("bearer_token_env_var")
    if isinstance(var, str) and os.environ.get(var):
        headers["Authorization"] = "Bearer " + os.environ[var]
    return headers if any(k.lower() == "authorization" for k in headers) else None


def post(url, headers, body, session, deadline):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    for key, value in headers.items():
        request.add_header(key, value)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/event-stream")
    request.add_header("MCP-Protocol-Version", PROTOCOL)
    if session:
        request.add_header("Mcp-Session-Id", session)
    with urllib.request.urlopen(request, timeout=max(0.5, deadline - time.monotonic())) as response:
        session = response.headers.get("Mcp-Session-Id") or session
        raw = response.read(2_000_000).decode("utf-8", "replace")
    if "id" not in body:
        return None, session
    lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")] or [raw]
    for line in reversed(lines):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == body["id"]:
            return message, session
    return None, session


def brief(url, headers, deadline):
    hello = {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "codex-memory-brief", "version": "1"},
    }
    _, session = post(
        url, headers, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": hello}, None, deadline
    )
    post(url, headers, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session, deadline)
    call = {"name": "memory.brief", "arguments": {}}
    reply, _ = post(
        url, headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": call}, session, deadline
    )
    result = (reply or {}).get("result") or {}
    if result.get("isError"):
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("text"), str):
        return structured["text"].strip() or None
    texts = [item.get("text") for item in result.get("content") or [] if isinstance(item, dict)]
    text = "\n".join(t for t in texts if isinstance(t, str) and t.strip())
    try:
        decoded = json.loads(text)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict) and isinstance(decoded.get("text"), str):
        text = decoded["text"]
    if not text and structured is not None:
        text = json.dumps(structured, ensure_ascii=False)
    return text.strip() or None


def log(home, row):
    try:
        path = home / "log" / "memory-brief-hook.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError:
        pass


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    found = servers(home)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"at": stamp, "session": payload.get("session_id"), "source": payload.get("source"), "servers": sorted(found)}
    deadline = time.monotonic() + BUDGET
    results: dict[str, str | None] = {}
    skipped: dict[str, str] = {}
    workers = []

    def load(name, entry):
        headers = headers_for(entry)
        if headers is None:
            skipped[name] = "no bearer in env"
            return
        try:
            results[name] = brief(str(entry["url"]), headers, deadline)
        except Exception as exc:
            skipped[name] = type(exc).__name__

    for name, entry in sorted(found.items()):
        worker = threading.Thread(target=load, args=(name, entry), daemon=True)
        worker.start()
        workers.append(worker)
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    parts = [(name, text) for name, text in sorted(results.items()) if text]
    context = ""
    if parts:
        share = max(500, CAP // len(parts))
        body = "\n\n".join(f"## {name} (memory.brief)\n{text[:share]}" for name, text in parts)
        context = (
            "Memory brief loaded at session start. It is background about the person you work for; "
            "use memory.search for anything it does not cover.\n\n" + body
        )
        output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
        sys.stdout.write(json.dumps(output))
    log(home, {**row, "loaded": [name for name, _ in parts], "skipped": dict(skipped), "bytes": len(context)})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
