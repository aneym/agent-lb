#!/usr/bin/env bash
# anthropic-auth.sh — drive agent-lb's Anthropic (Claude) OAuth flow from the CLI.
#
# Talks to the locally running agent-lb server over its dashboard API. No browser
# automation / Cloudflare bypass: you open the printed URL yourself, approve in
# Claude, then paste the returned `code#state` back here.
#
# Usage:
#   scripts/anthropic-auth.sh start                          # start a flow, print the Claude auth URL
#   scripts/anthropic-auth.sh complete <flowId> '<code#state>'  # finish login with the pasted code
#   scripts/anthropic-auth.sh status <flowId>                # check a flow's status
#   scripts/anthropic-auth.sh accounts                       # list Anthropic accounts + status
#
# Env: BASE_URL (default http://127.0.0.1:2455)
set -euo pipefail
BASE_URL="${BASE_URL:-http://127.0.0.1:2455}"
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
