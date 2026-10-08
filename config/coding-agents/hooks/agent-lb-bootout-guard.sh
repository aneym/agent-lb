#!/bin/bash
# PreToolUse guard: agent-lb restarts go through lb-restart (blue/green, 2026-09-25).
# A raw kickstart/bootout of the service or the front holds every new connection
# while the old process drains (10-95 s measured 2026-09-25) and can cut streams.
# Escape hatch when lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok
# Adopted into agent-lb 2026-10-08 (hook-dispatcher-4): edit this source only. It is a floor guard, so input it
# cannot read (jq missing or failing) is refused, never allowed as an empty command. jq reads the input itself (a
# missing `cat` before it read as an empty command), and a matcher that fails (grep missing or erroring, exit > 1) is
# told apart from no match (exit 1) and refuses (2026-10-08 review: a missing grep let a raw kickstart through).
JQ=/opt/homebrew/bin/jq
[ -x "$JQ" ] || JQ=jq
if ! CMD=$("$JQ" -r '.tool_input.command // empty' 2>/dev/null); then
  echo "BLOCKED: agent-lb-bootout-guard could not read the hook input (jq missing or failed), so the call is refused." >&2
  exit 2
fi
[ -z "$CMD" ] && exit 0
case "$CMD" in *launchctl*) ;; *) exit 0 ;; esac  # the pattern needs `launchctl`; the dispatcher's prefilter is this
printf '%s\n' "$CMD" | grep -qE 'launchctl[^|;&]*(bootout|kickstart|stop|kill)[^|;&]*com\.aneyman\.agent-lb(-front)?([^a-zA-Z0-9_-]|$)'
MATCH=$?
if [ "$MATCH" -gt 1 ]; then
  echo "BLOCKED: agent-lb-bootout-guard could not match the command (grep exit $MATCH: missing or failed), so the call is refused." >&2
  exit 2
fi
if [ "$MATCH" -eq 0 ]; then
  if [ ! -f "$HOME/.agent-lb/raw-restart.ok" ]; then
    {
      echo "BLOCKED: raw launchctl restart of agent-lb. Use the blue/green restart instead:"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" [--from <worktree> --files <paths>]"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" --check   # boot+health-check only"
      echo "Add --reload-plist after editing the launchd plist."
      echo "Only if lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok, then remove it."
    } >&2
    exit 2
  fi
fi
exit 0
