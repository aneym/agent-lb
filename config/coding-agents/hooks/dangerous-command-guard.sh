#!/bin/bash
# PreToolUse Bash floor guard: `rm -rf /...` and `DROP DATABASE|TABLE` need explicit user approval.
# Until 2026-10-08 this was an inline leaf in settings.json:
#   bash -c 'CMD=$(cat | jq -r ".tool_input.command // empty"); [ -z "$CMD" ] && exit 0; echo "$CMD" | grep -qiE ...'
# Review M3 (hook-dispatcher-5): with jq or grep failing, that shell turned the failure into exit 0 and allowed
# `psql -c "DROP DATABASE app"`. S44 moved it here: install-policy.py installs this file and registers it in place of
# the inline leaf, and the dispatcher runs it as a direct exec. Every read and the match are checked: jq must read
# exactly one JSON input, grep must answer match (0) or no match (1); anything else refuses. An allow ends with the
# receipt line `floor-ok dangerous-command-guard.sh` when the dispatcher asks for it (HOOK_FLOOR_RECEIPT); the
# dispatcher refuses an exit 0 without it. The match is the inline leaf's, unchanged (hook-dispatch.py pf_dangerous
# is its prefilter).
set -euo pipefail
refuse() {
  echo "BLOCKED: dangerous-command-guard $1, so this call is refused (the guard fails closed). Check jq and grep on PATH." >&2
  exit 2
}
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' dangerous-command-guard.sh; fi
  exit 0
}
CMD=$(jq -n -r 'input | .tool_input.command // empty' 2>/dev/null) || refuse "could not read the hook input (jq exit $?)"
[ -n "$CMD" ] || allow
MATCH=0
grep -qiE 'rm\s+-rf\s+/|DROP\s+(DATABASE|TABLE)' <<<"$CMD" || MATCH=$?
if [ "$MATCH" -eq 0 ]; then
  echo "BLOCKED: Dangerous command requires explicit user approval." >&2
  exit 2
fi
[ "$MATCH" -eq 1 ] || refuse "could not match the command (grep exit $MATCH: missing or failed)"
allow
