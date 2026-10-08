#!/bin/bash
# Keep Stripe Link CLI secrets out of transcripts (2026-09-25).
# - `link-cli auth status` prints an access token unless output is filtered.
# - `--full-output` prints the whole envelope, token included.
# - `--include card` prints card data unless it goes to --output-file.
# Adopted into agent-lb 2026-10-08 (S44, hook-dispatcher-5) from the hand-installed copy (sha256 cb71baf5): edit this
# source only. A floor guard: reads and matches are checked (set -euo pipefail; jq must read one JSON input, grep must
# answer match or no match), and anything else refuses. An allow ends with the receipt line `floor-ok link-cli-guard.sh`
# when the dispatcher asks for it (HOOK_FLOOR_RECEIPT); the dispatcher refuses an exit 0 without it. The matches are
# the earlier copy's, unchanged (hook-dispatch.py pf_link_cli is its prefilter).
set -euo pipefail
refuse() {
  echo "BLOCKED: link-cli-guard $1, so this call is refused (the guard fails closed). Check jq and grep on PATH." >&2
  exit 2
}
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' link-cli-guard.sh; fi
  exit 0
}
# matches <ERE>: 0 when the command matches, 1 when it does not; a failed grep refuses.
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
