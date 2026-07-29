#!/usr/bin/env bash
set -euo pipefail

LABEL="com.aneyman.agent-lb"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON_BIN="${PYTHON_BIN:-$REPO_DIR/.venv/bin/python}"
STATUS_URL="${CLAUDE_LB_LOCAL_URL:-http://127.0.0.1:2455}/api/federation/status"
TIMEOUT_SECONDS="${AGENT_LB_FEDERATION_INSTALL_TIMEOUT_SECONDS:-120}"
INTERVAL_SECONDS="300"
INSTANCE_ID="$(hostname -s | tr '[:upper:]' '[:lower:]')"
PEER_URL=""
TOKEN_FILE=""
MODE="install"

usage() {
  cat <<'EOF'
usage: install-federation-follower.sh [--peer-url URL] [--instance-id ID]
       [--token-file PATH] [--interval-seconds N] [--timeout-seconds N]
       [--print | --uninstall]

The federation token is read from AGENT_LB_FEDERATION_TOKEN or --token-file.
EOF
}

while (($#)); do
  case "$1" in
    --peer-url) PEER_URL="${2:-}"; shift 2 ;;
    --instance-id) INSTANCE_ID="${2:-}"; shift 2 ;;
    --token-file) TOKEN_FILE="${2:-}"; shift 2 ;;
    --interval-seconds) INTERVAL_SECONDS="${2:-}"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --print) MODE="print"; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for value_name in INTERVAL_SECONDS TIMEOUT_SECONDS; do
  value="${!value_name}"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: ${value_name,,} must be a positive integer" >&2
    exit 2
  fi
done

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "error: Python is required" >&2
  exit 1
fi

if [[ "$MODE" != "uninstall" ]]; then
  if [[ -z "$PEER_URL" || -z "$INSTANCE_ID" ]]; then
    echo "error: --peer-url and a non-empty --instance-id are required" >&2
    exit 2
  fi
  if [[ -n "$TOKEN_FILE" ]]; then
    if [[ ! -r "$TOKEN_FILE" ]]; then
      echo "error: token file is not readable: $TOKEN_FILE" >&2
      exit 2
    fi
    FEDERATION_TOKEN="$(<"$TOKEN_FILE")"
  else
    FEDERATION_TOKEN="${AGENT_LB_FEDERATION_TOKEN:-}"
  fi
  if [[ -z "$FEDERATION_TOKEN" ]]; then
    echo "error: set AGENT_LB_FEDERATION_TOKEN or pass --token-file" >&2
    exit 2
  fi
fi

print_environment() {
  printf '%s\n' \
    "AGENT_LB_LOCAL_INSTANCE_ID=$INSTANCE_ID" \
    "AGENT_LB_FEDERATION_TOKEN=<redacted>" \
    "AGENT_LB_FEDERATION_PEER_URL=${PEER_URL%/}" \
    "AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS=$INTERVAL_SECONDS"
}

if [[ "$MODE" == "print" ]]; then
  print_environment
  exit 0
fi

if [[ ! -f "$PLIST" ]]; then
  "$SCRIPT_DIR/install-service.sh"
fi

FOLLOWER_PLIST="$PLIST" \
FOLLOWER_MODE="$MODE" \
FOLLOWER_INSTANCE_ID="${INSTANCE_ID:-}" \
FOLLOWER_TOKEN="${FEDERATION_TOKEN:-}" \
FOLLOWER_PEER_URL="${PEER_URL%/}" \
FOLLOWER_INTERVAL_SECONDS="$INTERVAL_SECONDS" \
"$PYTHON_BIN" <<'PY'
from __future__ import annotations

import os
import plistlib
from pathlib import Path

plist_path = Path(os.environ["FOLLOWER_PLIST"])
plist = plistlib.loads(plist_path.read_bytes())
environment = dict(plist.get("EnvironmentVariables", {}))
keys = (
    "AGENT_LB_LOCAL_INSTANCE_ID",
    "AGENT_LB_FEDERATION_TOKEN",
    "AGENT_LB_FEDERATION_PEER_URL",
    "AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS",
)
if os.environ["FOLLOWER_MODE"] == "uninstall":
    for key in keys:
        environment.pop(key, None)
else:
    environment.update(
        {
            "AGENT_LB_LOCAL_INSTANCE_ID": os.environ["FOLLOWER_INSTANCE_ID"],
            "AGENT_LB_FEDERATION_TOKEN": os.environ["FOLLOWER_TOKEN"],
            "AGENT_LB_FEDERATION_PEER_URL": os.environ["FOLLOWER_PEER_URL"],
            "AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS": os.environ["FOLLOWER_INTERVAL_SECONDS"],
        }
    )
plist["EnvironmentVariables"] = environment
plistlib.dump(plist, plist_path.open("wb"), sort_keys=True)
PY

"$SCRIPT_DIR/install-service.sh"

if [[ "$MODE" == "uninstall" ]]; then
  echo "Removed federation follower configuration."
  exit 0
fi

deadline=$(($(date +%s) + TIMEOUT_SECONDS))
last_error="status endpoint unavailable"
while (($(date +%s) < deadline)); do
  if status="$(curl -fsS --connect-timeout 5 --max-time 10 "$STATUS_URL" 2>/dev/null)"; then
    result="$(STATUS_JSON="$status" "$PYTHON_BIN" <<'PY'
import json
import os

status = json.loads(os.environ["STATUS_JSON"])
mirror = status.get("mirror", {})
print("ready" if mirror.get("lastSuccessAt") else (mirror.get("lastError") or "mirror has not succeeded yet"))
PY
)"
    if [[ "$result" == "ready" ]]; then
      echo "Federation follower is ready."
      exit 0
    fi
    last_error="$result"
  fi
  sleep 1
done

echo "error: federation mirror did not succeed within ${TIMEOUT_SECONDS}s: $last_error" >&2
exit 1
