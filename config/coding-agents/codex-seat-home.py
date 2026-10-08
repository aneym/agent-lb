#!/usr/bin/env python3
"""Build a job-local CODEX_HOME; never read authentication contents.

Shell access stays workspace-write, with network for gh/loopback checks.
macOS Chromium bootstrap still requires an outside-sandbox capture process.
No MCP, apps, plugins, hooks or global rollout archive are inherited.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = Path(os.environ.get("CODEX_SOURCE_HOME", "~/.codex")).expanduser()
    destination = args.destination.expanduser().absolute()
    if destination.resolve() == source.resolve():
        parser.error("destination must be a job home, not the source Codex home")
    config = source / "config.toml"
    text = config.read_text().splitlines() if config.is_file() else []
    top = []
    for line in text:
        if line.lstrip().startswith("["):
            break
        top.append(line)
    matches = [re.match(r'\s*model_provider\s*=\s*"([^"\n]+)"', line) for line in top]
    provider = next((match.group(1) for match in matches if match), "agent-lb")
    lines = ['approval_policy = "never"', 'sandbox_mode = "workspace-write"']
    if provider:
        lines.append(f'model_provider = "{provider}"')
    lines += ["", "[sandbox_workspace_write]", "network_access = true", "", "[features]"]
    lines += [f"{feature} = false" for feature in (
        "apps", "plugins", "hooks", "memories", "multi_agent", "goals", "chronicle", "js_repl",
        "responses_websockets", "responses_websockets_v2",
    )]
    lines += ["", "[agents]", "max_depth = 1"]
    if provider:
        header = re.compile(r'\[\s*model_providers\.(?:"%s"|%s)\s*\]\s*(?:#.*)?$'
                            % (re.escape(provider), re.escape(provider)))
        block, active = [], False
        for line in text:
            if line.lstrip().startswith("["):
                active = bool(header.fullmatch(line.strip()))
            if active and not re.match(r"\s*supports_websockets\s*=", line):
                block.append(line)
        if not block:
            if text:
                parser.error("selected provider has no configuration table")
            block = ['[model_providers.agent-lb]', 'name = "Agent LB"',
                     'base_url = "http://127.0.0.1:2455/backend-api/codex"',
                     'wire_api = "responses"', 'requires_openai_auth = true']
        lines += [""] + block + ["supports_websockets = false"]
    destination.mkdir(parents=True, exist_ok=True)
    sessions = destination / "sessions"
    if sessions.is_symlink():
        sessions.unlink()  # Never traverse or delete the archive behind the link.
    sessions.mkdir(exist_ok=True)
    (destination / "config.toml").write_text("\n".join(lines) + "\n")
    auth = destination / "auth.json"
    source_auth = source / "auth.json"
    if source_auth.exists() and not auth.exists() and not auth.is_symlink():
        auth.symlink_to(source_auth.resolve())
    print(destination)


if __name__ == "__main__":
    main()
