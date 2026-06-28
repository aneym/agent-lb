#!/usr/bin/env bash
# install-service.sh — install/restart agent-lb as a macOS launchd user agent.
#
# Resolves the repo from this script's location, generates a LaunchAgent plist
# (label com.aneyman.agent-lb) pointing at the project venv binary, and bootstraps it.
# Regenerating an existing plist preserves operator-provided environment,
# arguments, and resource limits so reinstalling cannot silently switch data
# stores or trusted-access configuration.
# Guards against clobbering a localhost server already listening on port 2455
# under a different label.
#
# Usage:
#   scripts/install-service.sh            # install or restart the service
#   scripts/install-service.sh --print    # print the generated plist, no changes
#   scripts/install-service.sh --uninstall  # bootout + remove the plist
#
set -euo pipefail

LABEL="com.aneyman.agent-lb"
PORT=2455
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(realpath "$REPO_DIR")"
BIN="$REPO_DIR/.venv/bin/agent-lb"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.agent-lb"
PYTHON_BIN="${PYTHON_BIN:-$REPO_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

gen_plist() {
  if [[ -z "$PYTHON_BIN" ]]; then
    echo "error: python3 is required to generate the LaunchAgent plist" >&2
    exit 1
  fi

  INSTALL_AGENT_LB_LABEL="$LABEL" \
    INSTALL_AGENT_LB_BIN="$BIN" \
    INSTALL_AGENT_LB_REPO_DIR="$REPO_DIR" \
    INSTALL_AGENT_LB_PLIST="$PLIST" \
    INSTALL_AGENT_LB_LOG_DIR="$LOG_DIR" \
    "$PYTHON_BIN" <<'PY'
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path
from typing import Any


def _str_dict(value: Any, *, key: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be a dictionary")
    return {str(k): str(v) for k, v in value.items()}


def _program_args(value: Any, *, bin_path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [bin_path]
    return [bin_path, *(str(arg) for arg in value[1:])]


label = os.environ["INSTALL_AGENT_LB_LABEL"]
bin_path = os.environ["INSTALL_AGENT_LB_BIN"]
repo_dir = os.environ["INSTALL_AGENT_LB_REPO_DIR"]
plist_path = Path(os.environ["INSTALL_AGENT_LB_PLIST"])
log_dir = os.environ["INSTALL_AGENT_LB_LOG_DIR"]

existing: dict[str, Any] = {}
if plist_path.exists():
    try:
        loaded = plistlib.loads(plist_path.read_bytes())
    except Exception as exc:  # pragma: no cover - exercised through shell failure
        print(f"error: unable to read existing LaunchAgent plist {plist_path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if not isinstance(loaded, dict):
        print(f"error: existing LaunchAgent plist {plist_path} is not a dictionary", file=sys.stderr)
        raise SystemExit(1)
    existing = loaded

try:
    env = _str_dict(existing.get("EnvironmentVariables"), key="EnvironmentVariables")
except TypeError as exc:
    print(f"error: invalid existing LaunchAgent plist {plist_path}: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

env.setdefault("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
env.setdefault("AGENT_LB_METRICS_ENABLED", "true")
env.setdefault("AGENT_LB_METRICS_HOST", "127.0.0.1")
env.setdefault("AGENT_LB_METRICS_PORT", "9090")

plist: dict[str, Any] = {
    "Label": label,
    "ProgramArguments": _program_args(existing.get("ProgramArguments"), bin_path=bin_path),
    "WorkingDirectory": repo_dir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": f"{log_dir}/agent-lb.out.log",
    "StandardErrorPath": f"{log_dir}/agent-lb.err.log",
    "EnvironmentVariables": env,
}

for key in ("SoftResourceLimits", "HardResourceLimits"):
    if key in existing:
        plist[key] = existing[key]

plistlib.dump(plist, sys.stdout.buffer, sort_keys=True)
PY
}

label_loaded() {
  launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1
}

localhost_port_busy() {
  local listeners
  listeners="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "$listeners" ]] && grep -Eq "(127\.0\.0\.1|localhost|0\.0\.0\.0|\*):$PORT([[:space:]]|$)" <<<"$listeners"
}

write_plist() {
  local tmp_plist
  tmp_plist="$(mktemp "$PLIST.XXXXXX")"
  gen_plist >"$tmp_plist"
  mv "$tmp_plist" "$PLIST"
}

case "${1:-}" in
  --print)
    gen_plist
    exit 0
    ;;
  --uninstall)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled $LABEL"
    exit 0
    ;;
  "")
    ;;
  *)
    echo "usage: $0 [--print | --uninstall]" >&2
    exit 2
    ;;
esac

if [[ ! -x "$BIN" ]]; then
  echo "error: $BIN not found — run \`uv sync\` first" >&2
  exit 1
fi

# Port-conflict guard: refuse to touch a local server we don't own. Tailnet
# listeners on the same public port are allowed because this service binds
# 127.0.0.1 and Tailscale forwards to it.
if localhost_port_busy && ! label_loaded; then
  echo "error: localhost port $PORT is in use by another process — is agent-lb already running under a different service name?" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$PLIST")"

if label_loaded; then
  echo "Booting out existing $LABEL..."
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  # Immediate re-bootstrap after bootout fails with error 5 on macOS.
  sleep 5
fi

write_plist
launchctl bootstrap "gui/$UID" "$PLIST"

deadline=$(($(date +%s) + 30))
while (($(date +%s) < deadline)); do
  if health="$(curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null)"; then
    echo "agent-lb is up: $health"
    exit 0
  fi
  sleep 1
done

echo "error: agent-lb did not become healthy within 30s — check $LOG_DIR/agent-lb.err.log" >&2
exit 1
