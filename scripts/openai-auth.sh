#!/usr/bin/env bash
# openai-auth.sh — drive agent-lb's OpenAI (ChatGPT) OAuth flow from the CLI.
#
# Talks to the locally running agent-lb server over its dashboard API. Unlike
# the Anthropic flow, OpenAI is browser-callback-based: `start` spins up a
# localhost:1455 callback listener inside the SERVER process, then you open the
# returned authorizationUrl, approve in ChatGPT, and the browser redirect to
# localhost:1455 completes the flow automatically. There is no paste-back step.
# This only works when the browser runs on the same machine as the server.
#
# Usage:
#   scripts/openai-auth.sh start              # start a flow, print the ChatGPT auth URL
#   scripts/openai-auth.sh status <flowId>    # check a flow's status
#   scripts/openai-auth.sh accounts           # list OpenAI accounts + status
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
      -d '{"provider":"openai"}' | pp
    echo >&2
    echo "Open the authorizationUrl above in a browser logged into the target ChatGPT account." >&2
    echo "The flow completes automatically once you approve; check with:" >&2
    echo "  scripts/openai-auth.sh status <flowId>" >&2
    echo "Note: the callback lands on localhost:1455, so the browser must run on the same machine as the server." >&2
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
    provider = a.get("provider")
    # Legacy OpenAI accounts may omit provider (default) or carry null/"".
    if provider in (None, "", "openai"):
        print(a.get("status", "?"), "|", a.get("email") or a.get("accountId") or a.get("account_id"), "|", a.get("accountId") or a.get("account_id"))
'
    ;;
  *)
    echo "usage: $0 {start | status <flowId> | accounts}" >&2
    exit 2
    ;;
esac
