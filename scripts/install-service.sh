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
READY_TIMEOUT_SECONDS="${AGENT_LB_INSTALL_READY_TIMEOUT_SECONDS:-120}"
PORT_FREE_TIMEOUT_SECONDS="${AGENT_LB_INSTALL_PORT_FREE_TIMEOUT_SECONDS:-30}"

if [[ ! "$READY_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: AGENT_LB_INSTALL_READY_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi

if [[ ! "$PORT_FREE_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: AGENT_LB_INSTALL_PORT_FREE_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi

now_ms() {
  /usr/bin/perl -MTime::HiRes=time -e 'printf "%d\n", time() * 1000'
}

install_started_ms="$(now_ms)"
bootout_elapsed_ms=0

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

# launchd defaults to 256 open files; the proxy holds hundreds of keep-alive
# upstream sockets, so the default exhausts fds and turns into 500s on
# /v1/messages ("Too many open files" / "unable to open database file").
DEFAULT_FILE_LIMITS = {"SoftResourceLimits": 4096, "HardResourceLimits": 8192}

for key, default_files in DEFAULT_FILE_LIMITS.items():
    limits = existing.get(key)
    limits = dict(limits) if isinstance(limits, dict) else {}
    limits.setdefault("NumberOfFiles", default_files)
    plist[key] = limits

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
  bootout_started_ms="$(now_ms)"
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  # Wait for the old process to release localhost:PORT before rebinding.
  port_free_deadline=$(($(date +%s) + PORT_FREE_TIMEOUT_SECONDS))
  while (($(date +%s) < port_free_deadline)) && localhost_port_busy; do
    sleep 0.1
  done
  if localhost_port_busy; then
    # Never abort here: the job is already booted out, and exiting now leaves
    # nothing bootstrapped (caused the 2026-07-11 outage). A draining old
    # process just delays the rebind — KeepAlive retries until the port frees.
    echo "warning: localhost port $PORT still in use ${PORT_FREE_TIMEOUT_SECONDS}s after bootout — bootstrapping anyway; launchd will retry the bind" >&2
  fi
  bootout_elapsed_ms=$(($(now_ms) - bootout_started_ms))
fi

write_plist

# Bootstrap may fail with error 5 if launchd is still tearing down the old job;
# retry with short bounded backoff instead of imposing a fixed cooldown on every restart.
bootstrap_started_ms="$(now_ms)"
bootstrap_ok=false
for attempt in 1 2 3; do
  if launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null; then
    bootstrap_ok=true
    break
  fi
  case "$attempt" in
    1) sleep 0.1 ;;
    2) sleep 0.25 ;;
    *) sleep 0.5 ;;
  esac
  # launchctl can return EIO while the GUI domain's plist watcher has already
  # loaded the freshly written job. Treat the observable loaded state as the
  # source of truth instead of reporting a false restart failure.
  if label_loaded; then
    echo "Bootstrap command returned nonzero, but $LABEL is loaded; continuing."
    bootstrap_ok=true
    break
  fi
  if [[ "$attempt" != 3 ]]; then
    echo "Bootstrap attempt $attempt failed, retrying after bounded backoff..." >&2
  fi
done
bootstrap_elapsed_ms=$(($(now_ms) - bootstrap_started_ms))
if [[ "$bootstrap_ok" != true ]]; then
  echo "error: launchctl bootstrap failed after 3 attempts" >&2
  exit 1
fi

# Bootstrap alone frequently leaves the job loaded-but-not-running on macOS;
# kickstart guarantees the process actually spawns with the new plist.
launchctl kickstart -k "gui/$UID/$LABEL" >/dev/null 2>&1 || true

process_started_ms="$(now_ms)"
startup_elapsed_ms=-1
deadline=$(($(date +%s) + READY_TIMEOUT_SECONDS))
readiness_rebootstrap_attempts=0
while (($(date +%s) < deadline)); do
  # A GUI-domain plist watcher can briefly report the label loaded after an
  # EIO bootstrap, then drop it before the process spawns. Recover inside the
  # bounded readiness window instead of polling a job that no longer exists.
  if ! label_loaded && ((readiness_rebootstrap_attempts < 3)); then
    readiness_rebootstrap_attempts=$((readiness_rebootstrap_attempts + 1))
    echo "LaunchAgent disappeared during readiness; re-bootstrap attempt $readiness_rebootstrap_attempts..." >&2
    launchctl bootstrap "gui/$UID" "$PLIST" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$UID/$LABEL" >/dev/null 2>&1 || true
  fi
  if ((startup_elapsed_ms < 0)) && curl -fsS "http://127.0.0.1:$PORT/health/startup" >/dev/null 2>&1; then
    startup_elapsed_ms=$(($(now_ms) - process_started_ms))
  fi
  if health="$(curl -fsS "http://127.0.0.1:$PORT/health/ready" 2>/dev/null)"; then
    ready_elapsed_ms=$(($(now_ms) - process_started_ms))
    total_elapsed_ms=$(($(now_ms) - install_started_ms))
    echo "agent-lb is ready: $health"
    echo "startup_timing_ms bootout=$bootout_elapsed_ms bootstrap=$bootstrap_elapsed_ms startup=$startup_elapsed_ms ready=$ready_elapsed_ms total=$total_elapsed_ms"
    exit 0
  fi
  sleep 0.1
done

total_elapsed_ms=$(($(now_ms) - install_started_ms))
echo "startup_timing_ms bootout=$bootout_elapsed_ms bootstrap=$bootstrap_elapsed_ms startup=$startup_elapsed_ms ready=timeout total=$total_elapsed_ms" >&2
echo "error: agent-lb did not become ready within ${READY_TIMEOUT_SECONDS}s — check $LOG_DIR/agent-lb.err.log" >&2
exit 1
