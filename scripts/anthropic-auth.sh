#!/usr/bin/env bash














set -euo pipefail







_default_base_url() {
  local peer_url
  peer_url="$(launchctl print "gui/$(id -u)/com.aneyman.agent-lb" 2>/dev/null \
    | awk -F' => ' '/AGENT_LB_FEDERATION_PEER_URL/ {print $2; exit}')"
  echo "${peer_url:-http://127.0.0.1:2455}"
}

BASE_URL="${BASE_URL:-$(_default_base_url)}"
cmd="${1:-}"
pp() { python3 -m json.tool 2>/dev/null || cat; }

case "$cmd" in
  start)
    curl -sS -X POST "$BASE_URL/api/oauth/start" \
      -H 'Content-Type: application/json' \
      -d '{"provider":"anthropic"}' | pp
    ;;
  complete)
    flow="${2:?flowId required}"; cb="${3:?code#state required}"
    body="$(python3 -c 'import json,sys; print(json.dumps({"flowId":sys.argv[1],"callbackUrl":sys.argv[2]}))' "$flow" "$cb")"
    curl -sS -X POST "$BASE_URL/api/oauth/manual-callback" \
      -H 'Content-Type: application/json' -d "$body" | pp
    ;;
  status)
    flow="${2:?flowId required}"
    curl -sS "$BASE_URL/api/oauth/status?flowId=$flow" | pp
    ;;
  accounts|list)
    curl -sS "$BASE_URL/api/accounts" | python3 -c '
import sys, json
d = json.load(sys.stdin)
rows = d if isinstance(d, list) else d.get("accounts", d.get("data", []))
for a in rows:
    if a.get("provider") == "anthropic":
        sub = (a.get("subscription") or {}).get("status") or "-"
        state = a.get("status", "?")
        if state == "active" and sub == "canceled":
            state = "unsubscribed"
        print(state, "|", "sub:" + sub, "|", a.get("email") or a.get("accountId"), "|", a.get("accountId"))
'
    ;;
  *)
    echo "usage: $0 {start | complete <flowId> <code#state> | status <flowId> | accounts}" >&2
    exit 2
    ;;
esac
