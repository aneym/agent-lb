#!/usr/bin/env bash
# Install the persistent loopback HTTPS proxy used by Claude Desktop.
set -euo pipefail

LABEL="com.aneyman.agent-lb-claude-desktop-proxy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(realpath "$REPO_DIR")"
LAUNCHER="$REPO_DIR/clients/claude-lb-launch"
TARGET_HOME="${CLAUDE_DESKTOP_PROXY_HOME:-$HOME}"
PORT="${CLAUDE_DESKTOP_PROXY_PORT:-2458}"
UPSTREAM="${CLAUDE_DESKTOP_PROXY_UPSTREAM:-http://127.0.0.1:2455}"
PYTHON_BIN="${CLAUDE_DESKTOP_PROXY_PYTHON:-$(command -v python3 || true)}"
PLIST="$TARGET_HOME/Library/LaunchAgents/$LABEL.plist"
STATE_DIR="$TARGET_HOME/.agent-lb"
SETTINGS="$TARGET_HOME/.claude/settings.json"
BACKUP="$STATE_DIR/claude-desktop-proxy-settings-backup.json"
CA_CERT="$STATE_DIR/tls/ca.pem"
PROXY_URL="http://127.0.0.1:$PORT"
READY_ATTEMPTS="${CLAUDE_DESKTOP_PROXY_READY_ATTEMPTS:-60}"
PORT_FREE_ATTEMPTS="${CLAUDE_DESKTOP_PROXY_PORT_FREE_ATTEMPTS:-40}"

usage() {
  echo "usage: $0 [--print | --uninstall]" >&2
}

fail() {
  echo "error: $*" >&2
  exit 1
}

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1 || PORT > 65535)); then
  fail "CLAUDE_DESKTOP_PROXY_PORT must be an integer from 1 through 65535"
fi
if [[ ! "$READY_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  fail "CLAUDE_DESKTOP_PROXY_READY_ATTEMPTS must be a positive integer"
fi
if [[ ! "$PORT_FREE_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  fail "CLAUDE_DESKTOP_PROXY_PORT_FREE_ATTEMPTS must be a positive integer"
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  fail "python3 is required (set CLAUDE_DESKTOP_PROXY_PYTHON to its absolute path)"
fi

gen_plist() {
  INSTALL_LABEL="$LABEL" \
    INSTALL_PYTHON="$PYTHON_BIN" \
    INSTALL_LAUNCHER="$LAUNCHER" \
    INSTALL_PORT="$PORT" \
    INSTALL_UPSTREAM="$UPSTREAM" \
    INSTALL_REPO="$REPO_DIR" \
    INSTALL_STATE_DIR="$STATE_DIR" \
    "$PYTHON_BIN" <<'PY'
from __future__ import annotations

import os
import plistlib
import sys

state_dir = os.environ["INSTALL_STATE_DIR"]
plist = {
    "Label": os.environ["INSTALL_LABEL"],
    "ProgramArguments": [
        os.environ["INSTALL_PYTHON"],
        os.environ["INSTALL_LAUNCHER"],
        "--desktop-proxy",
        os.environ["INSTALL_PORT"],
        os.environ["INSTALL_UPSTREAM"],
    ],
    "WorkingDirectory": os.environ["INSTALL_REPO"],
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": f"{state_dir}/claude-desktop-proxy.out.log",
    "StandardErrorPath": f"{state_dir}/claude-desktop-proxy.err.log",
}
plistlib.dump(plist, sys.stdout.buffer, sort_keys=True)
PY
}

plist_is_owned() {
  [[ ! -e "$PLIST" ]] && return 0
  INSTALL_PLIST="$PLIST" INSTALL_LABEL="$LABEL" INSTALL_LAUNCHER="$LAUNCHER" \
    "$PYTHON_BIN" <<'PY'
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

path = Path(os.environ["INSTALL_PLIST"])
try:
    value = plistlib.loads(path.read_bytes())
except Exception as exc:
    print(f"error: invalid LaunchAgent plist {path}: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc
args = value.get("ProgramArguments") if isinstance(value, dict) else None
owned = (
    isinstance(value, dict)
    and value.get("Label") == os.environ["INSTALL_LABEL"]
    and isinstance(args, list)
    and os.environ["INSTALL_LAUNCHER"] in args
    and "--desktop-proxy" in args
)
if not owned:
    print(f"error: refusing to replace unowned LaunchAgent plist {path}", file=sys.stderr)
    raise SystemExit(1)
PY
}

settings_action() {
  local action="$1"
  INSTALL_SETTINGS_ACTION="$action" \
    INSTALL_SETTINGS="$SETTINGS" \
    INSTALL_BACKUP="$BACKUP" \
    INSTALL_PROXY_URL="$PROXY_URL" \
    INSTALL_CA_CERT="$CA_CERT" \
    "$PYTHON_BIN" <<'PY'
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

action = os.environ["INSTALL_SETTINGS_ACTION"]
settings_path = Path(os.environ["INSTALL_SETTINGS"])
backup_path = Path(os.environ["INSTALL_BACKUP"])
owned = {
    "HTTPS_PROXY": os.environ["INSTALL_PROXY_URL"],
    "https_proxy": os.environ["INSTALL_PROXY_URL"],
    "NODE_EXTRA_CA_CERTS": os.environ["INSTALL_CA_CERT"],
}


def load_json(path: Path, *, missing: Any) -> Any:
    if not path.exists():
        return missing
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: invalid JSON in {path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def settings_document() -> tuple[dict[str, Any], dict[str, Any]]:
    document = load_json(settings_path, missing={})
    if not isinstance(document, dict):
        print(f"error: {settings_path} must contain a JSON object", file=sys.stderr)
        raise SystemExit(1)
    env = document.get("env", {})
    if not isinstance(env, dict):
        print(f"error: env in {settings_path} must be a JSON object", file=sys.stderr)
        raise SystemExit(1)
    return document, env


def backup_document() -> dict[str, Any] | None:
    backup = load_json(backup_path, missing=None)
    if backup is None:
        return None
    if not isinstance(backup, dict) or backup.get("version") != 1:
        print(f"error: invalid desktop proxy checkpoint {backup_path}", file=sys.stderr)
        raise SystemExit(1)
    owner = backup.get("owned")
    previous = backup.get("previous")
    if not isinstance(owner, dict) or not isinstance(previous, dict):
        print(f"error: invalid desktop proxy checkpoint {backup_path}", file=sys.stderr)
        raise SystemExit(1)
    return backup


def atomic_write_json(path: Path, value: dict[str, Any], *, default_mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else default_mode
    file_descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, indent=2, sort_keys=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def validate_install() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    document, env = settings_document()
    backup = backup_document()
    if backup is not None and backup["owned"] != owned:
        print(
            "error: an existing desktop proxy checkpoint belongs to different settings; "
            "uninstall it before changing the port or home",
            file=sys.stderr,
        )
        raise SystemExit(1)
    conflicts = [key for key, value in owned.items() if key in env and env[key] != value]
    if conflicts:
        joined = ", ".join(conflicts)
        print(
            f"error: refusing to overwrite conflicting Claude settings env values: {joined}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return document, env, backup


if action in {"preflight", "install"}:
    document, env, backup = validate_install()
    if action == "preflight":
        raise SystemExit(0)
    if backup is None:
        previous = {
            key: {"present": key in env, **({"value": env[key]} if key in env else {})}
            for key in owned
        }
        backup = {"version": 1, "owned": owned, "previous": previous}
        atomic_write_json(backup_path, backup, default_mode=0o600)
    env.update(owned)
    document["env"] = env
    atomic_write_json(settings_path, document, default_mode=0o600)
    raise SystemExit(0)

if action in {"uninstall-check", "uninstall"}:
    document, env = settings_document()
    backup = backup_document()
    if action == "uninstall-check" or backup is None:
        raise SystemExit(0)
    owner = backup["owned"]
    previous = backup["previous"]
    for key, installed_value in owner.items():
        if env.get(key) != installed_value:
            continue
        entry = previous.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("present"), bool):
            print(f"error: invalid prior value for {key} in {backup_path}", file=sys.stderr)
            raise SystemExit(1)
        if entry["present"]:
            env[key] = entry.get("value")
        else:
            env.pop(key, None)
    if env:
        document["env"] = env
    else:
        document.pop("env", None)
    atomic_write_json(settings_path, document, default_mode=0o600)
    backup_path.unlink()
    raise SystemExit(0)

print(f"error: unknown settings action {action}", file=sys.stderr)
raise SystemExit(2)
PY
}

label_loaded() {
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1
}

listener_present() {
  local listeners
  listeners="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "$listeners" ]]
}

write_plist() {
  local temp_plist
  temp_plist="$(mktemp "$PLIST.tmp.XXXXXX")"
  if ! gen_plist >"$temp_plist"; then
    rm -f "$temp_plist"
    return 1
  fi
  chmod 0644 "$temp_plist"
  mv "$temp_plist" "$PLIST"
}

bootstrap_agent() {
  local attempt
  for attempt in 1 2 3; do
    if launchctl bootstrap "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || label_loaded; then
      launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
      return 0
    fi
    sleep 0.25
  done
  return 1
}

wait_for_no_listener() {
  local attempt
  for ((attempt = 0; attempt < PORT_FREE_ATTEMPTS; attempt++)); do
    listener_present || return 0
    sleep 0.25
  done
  return 1
}

proxy_ready() {
  label_loaded || return 1
  listener_present || return 1
  curl -fsS --max-time 3 \
    --proxy "$PROXY_URL" \
    --cacert "$CA_CERT" \
    "https://api.anthropic.com/health" >/dev/null 2>&1
}

wait_for_proxy() {
  local attempt
  for ((attempt = 0; attempt < READY_ATTEMPTS; attempt++)); do
    proxy_ready && return 0
    sleep 0.25
  done
  return 1
}

mode="${1:-}"
case "$mode" in
  --print)
    {
      echo "Preview only; no files or processes will be changed."
      echo "LaunchAgent: $LABEL"
      echo "Proxy: $PROXY_URL -> $UPSTREAM"
      echo "Claude settings: $SETTINGS"
      echo "Rollback: --uninstall restores installer-owned settings from $BACKUP when unchanged."
    } >&2
    gen_plist
    exit 0
    ;;
  --uninstall)
    plist_is_owned
    if [[ ! -e "$PLIST" ]] && label_loaded; then
      fail "refusing to alter loaded $LABEL without its owned plist at $PLIST"
    fi
    settings_action uninstall-check
    settings_action uninstall
    if [[ -e "$PLIST" ]]; then
      launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        label_loaded || break
        sleep 0.1
      done
      label_loaded && fail "LaunchAgent $LABEL remained loaded; plist was preserved"
      rm -f "$PLIST"
    fi
    echo "Uninstalled $LABEL"
    exit 0
    ;;
  "")
    ;;
  *)
    usage
    exit 2
    ;;
esac

[[ -x "$LAUNCHER" ]] || fail "$LAUNCHER is not executable"
command -v launchctl >/dev/null 2>&1 || fail "launchctl is required"
command -v lsof >/dev/null 2>&1 || fail "lsof is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"

# Validate every target before changing launchd or user settings.
plist_is_owned
settings_action preflight
if [[ ! -e "$PLIST" ]] && label_loaded; then
  fail "refusing to replace loaded $LABEL without its owned plist at $PLIST"
fi
was_loaded=false
label_loaded && was_loaded=true
if listener_present && [[ "$was_loaded" != true ]]; then
  fail "localhost port $PORT is already used by a process not owned by $LABEL"
fi

mkdir -p "$STATE_DIR" "$(dirname "$PLIST")"
prior_plist=""
if [[ -e "$PLIST" ]]; then
  prior_plist="$(mktemp "$STATE_DIR/claude-desktop-proxy-plist.XXXXXX")"
  cp "$PLIST" "$prior_plist"
fi

rollback_agent() {
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  if [[ -n "$prior_plist" && -e "$prior_plist" ]]; then
    mv "$prior_plist" "$PLIST"
    prior_plist=""
    if [[ "$was_loaded" == true ]]; then
      launchctl bootstrap "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
      launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    fi
  else
    rm -f "$PLIST"
  fi
}

if [[ "$was_loaded" == true ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  if ! wait_for_no_listener; then
    rollback_agent
    fail "localhost port $PORT did not become free after stopping $LABEL"
  fi
fi

if ! write_plist || ! bootstrap_agent; then
  rollback_agent
  fail "unable to bootstrap LaunchAgent $LABEL"
fi
if ! wait_for_proxy; then
  rollback_agent
  fail "desktop proxy did not pass proxied health readiness; Claude settings were not changed"
fi

# Recheck for a concurrent settings edit, then make the ownership checkpoint and
# settings cutover atomically per file. On failure, restore the prior agent.
if ! settings_action install; then
  rollback_agent
  fail "Claude settings changed during installation; LaunchAgent was rolled back"
fi

[[ -n "$prior_plist" ]] && rm -f "$prior_plist"
echo "Installed $LABEL on $PROXY_URL; Claude settings now route through agent-lb."
