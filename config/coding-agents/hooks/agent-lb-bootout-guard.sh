#!/bin/bash
# PreToolUse guard: agent-lb restarts go through lb-restart (blue/green, 2026-09-25).
# A raw kickstart/bootout of the service or the front holds every new connection
# while the old process drains (10-95 s measured 2026-09-25) and can cut streams.
# Escape hatch when lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok
# Adopted into agent-lb 2026-10-08 (hook-dispatcher-4): edit this source only. It is a floor guard, so input it
# cannot read (jq missing or failing) is refused, never allowed as an empty command. jq reads the input itself (a
# missing `cat` before it read as an empty command), and a matcher that fails (grep missing or erroring, exit > 1) is
# told apart from no match (exit 1) and refuses (2026-10-08 review: a missing grep let a raw kickstart through).
# S44 (2026-10-08): reads are checked (set -euo pipefail) and jq must read exactly one JSON input (`input` fails on an
# empty one); an allow ends with the receipt line `floor-ok agent-lb-bootout-guard.sh` when the dispatcher asks for it
# (HOOK_FLOOR_RECEIPT), and the dispatcher refuses an exit 0 without it.
# Guard trim (Alex, 2026-10-08 09:18 ET): a kept floor (keeps the orchestrators up). Its message names the floor and
# it accepts the Rails CoS approval record: a trailing `# alex-approval: <path>` naming an existing
# ~/.agent-rails/lanes/orchestrator-refs/alex-*-2026-*.md that carries a time.
set -euo pipefail
FLOOR="floor: keeps the orchestrators up (agent-lb carries every agent session)"
APPROVAL="Approved restarts pass with a trailing '# alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-<topic>-2026-<date>.md' (a first-hand Alex quote with a time)."
approved() {
  local ref
  ref=$(printf '%s\n' "$CMD" | sed -nE 's/.*#[[:space:]]*alex-approval:[[:space:]]*([^[:space:]]+).*/\1/p' | head -n 1)
  [ -n "$ref" ] || return 1
  case "$ref" in "~/"*) ref="$HOME/${ref#\~/}" ;; '$HOME/'*) ref="$HOME/${ref#\$HOME/}" ;; esac
  case "$ref" in *..*) return 1 ;; "$HOME"/.agent-rails/lanes/orchestrator-refs/alex-*-2026-*.md) ;; *) return 1 ;; esac
  [ -f "$ref" ] && grep -qE '[0-9]{1,2}:[0-9]{2}' "$ref"
}
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' agent-lb-bootout-guard.sh; fi
  exit 0
}
JQ=/opt/homebrew/bin/jq
[ -x "$JQ" ] || JQ=jq
if ! CMD=$("$JQ" -n -r 'input | .tool_input.command // empty' 2>/dev/null); then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not read the hook input (jq missing or failed), so the call is refused." >&2
  exit 2
fi
[ -n "$CMD" ] || allow
case "$CMD" in *launchctl*) ;; *) allow ;; esac  # the pattern needs `launchctl`; the dispatcher's prefilter is this
MATCH=0
grep -qE 'launchctl[^|;&]*(bootout|kickstart|stop|kill)[^|;&]*com\.aneyman\.agent-lb(-front)?([^a-zA-Z0-9_-]|$)' <<<"$CMD" || MATCH=$?
if [ "$MATCH" -gt 1 ]; then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not match the command (grep exit $MATCH: missing or failed), so the call is refused." >&2
  exit 2
fi
if [ "$MATCH" -eq 0 ]; then
  if [ ! -f "$HOME/.agent-lb/raw-restart.ok" ] && ! approved; then
    {
      echo "BLOCKED ($FLOOR): raw launchctl restart of agent-lb. Use the blue/green restart instead:"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" [--from <worktree> --files <paths>]"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" --check   # boot+health-check only"
      echo "Add --reload-plist after editing the launchd plist."
      echo "Only if lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok, then remove it."
      echo "$APPROVAL"
    } >&2
    exit 2
  fi
fi
allow
