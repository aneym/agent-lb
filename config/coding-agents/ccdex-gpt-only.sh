#!/usr/bin/env bash

if [[ "${CLAUDE_LB_CODEX_MODE:-}" != "1" ]]; then
  exit 0
fi

input=$(cat)
tool_name=$(jq -r '.tool_name // empty' <<<"$input")
reason=''

case "$tool_name" in
  Agent|Workflow)
    reason='CCDEX only permits the latest GPT. Do not use Agent or Workflow; use the GPT main loop or ccdex-worker.'
    ;;
  Bash)
    command=$(jq -r '.tool_input.command // empty' <<<"$input")
    if grep -Eq '(^|[;&|][[:space:]]*)(command[[:space:]]+)?(claude|cursor-agent|gemini|droid)([[:space:]]|$)' <<<"$command"; then
      reason='CCDEX only permits the latest GPT for agent work. Non-GPT agent CLIs are blocked; use the GPT main loop or ccdex-worker.'
    fi
    ;;
esac

if [[ -z "$reason" ]]; then
  exit 0
fi

jq -n --arg reason "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'
