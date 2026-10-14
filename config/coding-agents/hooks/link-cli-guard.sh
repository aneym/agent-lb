#!/bin/bash









set -euo pipefail
refuse() {
  echo "BLOCKED: link-cli-guard $1, so this call is refused (the guard fails closed). Check jq and grep on PATH." >&2
  exit 2
}
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' link-cli-guard.sh; fi
  exit 0
}

matches() {
  local status=0
  grep -qE "$1" <<<"$CMD" || status=$?
  [ "$status" -le 1 ] || refuse "could not match the command (grep exit $status: missing or failed)"
  return "$status"
}
CMD=$(jq -n -r 'input | .tool_input.command // empty' 2>/dev/null) || refuse "could not read the hook input (jq exit $?)"
[ -n "$CMD" ] || allow
matches 'link-cli' || allow
if matches 'link-cli[^;&|]*--full-output'; then
  echo "BLOCKED: link-cli --full-output prints the access token. Use --filter-output <keys>." >&2; exit 2
fi
if matches 'link-cli[^;&|]*auth[[:space:]]+status' && ! matches 'link-cli[^;&|]*--filter-output'; then
  echo "BLOCKED: link-cli auth status prints an access token. Run it with --filter-output authenticated." >&2; exit 2
fi
if matches 'link-cli[^;&|]*--include[[:space:]=]+card' && ! matches 'link-cli[^;&|]*--output-file'; then
  echo "BLOCKED: --include card must write to --output-file, never stdout. Never print card data." >&2; exit 2
fi
allow
