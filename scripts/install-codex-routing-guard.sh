#!/usr/bin/env bash
# Install the Codex desktop routing guard as a per-user macOS LaunchAgent.
set -euo pipefail

LABEL="com.aneyman.agent-lb-codex-routing-guard"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
GUARD="$REPO_DIR/clients/codex-routing-guard"
TARGET_HOME="${CODEX_ROUTING_GUARD_HOME:-$HOME}"
CONFIG_REQUESTED="${CODEX_ROUTING_GUARD_CONFIG:-$TARGET_HOME/.codex/config.toml}"
DEFAULT_PYTHON="$REPO_DIR/.venv/bin/python"
if [[ ! -x "$DEFAULT_PYTHON" ]]; then
  DEFAULT_PYTHON="$(command -v python3 || true)"
fi
PYTHON_BIN="${CODEX_ROUTING_GUARD_PYTHON:-$DEFAULT_PYTHON}"
INTERVAL="${CODEX_ROUTING_GUARD_INTERVAL:-300}"
PLIST="$TARGET_HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$TARGET_HOME/.agent-lb"
DOMAIN="gui/$(id -u)"

usage() {
  echo "usage: $0 [--print | --uninstall]" >&2
}

fail() {
  echo "error: $*" >&2
  exit 1
}

[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "python3 is required"
[[ "$INTERVAL" =~ ^[1-9][0-9]*$ ]] || fail "CODEX_ROUTING_GUARD_INTERVAL must be a positive integer"
[[ -x "$GUARD" ]] || fail "$GUARD is not executable"
[[ -f "$CONFIG_REQUESTED" ]] || fail "Codex config does not exist: $CONFIG_REQUESTED"
CONFIG="$(INSTALL_CONFIG="$CONFIG_REQUESTED" "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import os
print(Path(os.environ["INSTALL_CONFIG"]).resolve(strict=True))
PY
)" || fail "unable to resolve Codex config: $CONFIG_REQUESTED"

select_provider() {
  if [[ -n "${CODEX_ROUTING_GUARD_PROVIDER:-}" ]]; then
    printf '%s\n' "$CODEX_ROUTING_GUARD_PROVIDER"
    return
  fi
  INSTALL_CONFIG="$CONFIG" "$PYTHON_BIN" <<'PY'
from __future__ import annotations
import os
import tomllib
from pathlib import Path

path = Path(os.environ["INSTALL_CONFIG"])
try:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
    raise SystemExit(f"error: cannot select provider from {path}: {exc}") from exc
providers = document.get("model_providers", {})
active = document.get("model_provider")
expected = {
    "base_url": "http://127.0.0.1:2455/backend-api/codex",
    "wire_api": "responses",
    "supports_websockets": True,
    "requires_openai_auth": True,
}
def correct(name: object) -> bool:
    value = providers.get(name) if isinstance(name, str) and isinstance(providers, dict) else None
    return isinstance(value, dict) and all(value.get(key) == expected_value for key, expected_value in expected.items())
if correct(active):
    print(active)
else:
    known = next((name for name in ("agent-lb", "codex-lb") if correct(name)), None)
    print(known or "agent-lb")
PY
}

PROVIDER="$(select_provider)" || exit $?
[[ "$PROVIDER" =~ ^[A-Za-z0-9_-]+$ ]] || fail "invalid provider name: $PROVIDER"

label_loaded() {
  launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
}

gen_plist() {
  INSTALL_LABEL="$LABEL" INSTALL_PYTHON="$PYTHON_BIN" INSTALL_GUARD="$GUARD" \
    INSTALL_CONFIG="$CONFIG" INSTALL_PROVIDER="$PROVIDER" INSTALL_INTERVAL="$INTERVAL" \
    INSTALL_LOG_DIR="$LOG_DIR" "$PYTHON_BIN" <<'PY'
from __future__ import annotations
import os
import plistlib
import sys

plist = {
    "Label": os.environ["INSTALL_LABEL"],
    "ProgramArguments": [
        os.environ["INSTALL_PYTHON"],
        os.environ["INSTALL_GUARD"],
        "--config",
        os.environ["INSTALL_CONFIG"],
        "--provider",
        os.environ["INSTALL_PROVIDER"],
    ],
    "EnvironmentVariables": {"CODEX_ROUTING_GUARD_QUIET_ERRORS": "1"},
    "RunAtLoad": True,
    "WatchPaths": [os.environ["INSTALL_CONFIG"]],
    "StartInterval": int(os.environ["INSTALL_INTERVAL"]),
    "ProcessType": "Background",
    "StandardOutPath": f'{os.environ["INSTALL_LOG_DIR"]}/codex-routing-guard.launchd.out.log',
    "StandardErrorPath": f'{os.environ["INSTALL_LOG_DIR"]}/codex-routing-guard.launchd.err.log',
}
plistlib.dump(plist, sys.stdout.buffer, sort_keys=True)
PY
}

plist_is_owned() {
  [[ ! -e "$PLIST" ]] && return 0
  INSTALL_PLIST="$PLIST" INSTALL_LABEL="$LABEL" INSTALL_GUARD="$GUARD" "$PYTHON_BIN" <<'PY'
from __future__ import annotations
import os
import plistlib
from pathlib import Path

path = Path(os.environ["INSTALL_PLIST"])
try:
    value = plistlib.loads(path.read_bytes())
except Exception as exc:
    raise SystemExit(f"error: invalid LaunchAgent plist {path}: {exc}") from exc
args = value.get("ProgramArguments") if isinstance(value, dict) else None
if not (
    isinstance(value, dict)
    and value.get("Label") == os.environ["INSTALL_LABEL"]
    and isinstance(args, list)
    and os.environ["INSTALL_GUARD"] in args
):
    raise SystemExit(f"error: refusing to replace unowned LaunchAgent plist {path}")
PY
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

mode="${1:-}"
case "$mode" in
  --print)
    {
      echo "Preview only; no files or processes will be changed."
      echo "LaunchAgent: $LABEL"
      echo "Config: $CONFIG"
      echo "Provider: $PROVIDER"
      echo "Interval: ${INTERVAL}s"
    } >&2
    gen_plist
    exit 0
    ;;
  --uninstall)
    plist_is_owned
    if [[ -e "$PLIST" ]]; then
      launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        label_loaded || break
        sleep 0.1
      done
      label_loaded && fail "LaunchAgent $LABEL remained loaded; plist was preserved"
      rm -f "$PLIST"
    elif label_loaded; then
      fail "refusing to alter loaded $LABEL without its owned plist at $PLIST"
    fi
    echo "Uninstalled $LABEL; valid Codex routing was left in place."
    exit 0
    ;;
  "") ;;
  *) usage; exit 2 ;;
esac

command -v launchctl >/dev/null 2>&1 || fail "launchctl is required"
plist_is_owned
if [[ ! -e "$PLIST" ]] && label_loaded; then
  fail "refusing to replace loaded $LABEL without its owned plist at $PLIST"
fi

# Validate and converge the config before changing launchd.
"$PYTHON_BIN" "$GUARD" --config "$CONFIG" --provider "$PROVIDER"
mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
was_loaded=false
label_loaded && was_loaded=true
if [[ "$was_loaded" == true ]]; then
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    label_loaded || break
    sleep 0.1
  done
  label_loaded && fail "LaunchAgent $LABEL remained loaded; existing plist was preserved"
fi
write_plist
launchctl bootstrap "$DOMAIN" "$PLIST" >/dev/null
launchctl kickstart -k "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
label_loaded || fail "LaunchAgent $LABEL did not load"
"$PYTHON_BIN" "$GUARD" --config "$CONFIG" --provider "$PROVIDER"
echo "Installed $LABEL for provider $PROVIDER; watching $CONFIG."
