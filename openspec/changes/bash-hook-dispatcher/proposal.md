# One PreToolUse(Bash) dispatcher in place of fifteen hook processes

## Why
Studio offload all-stop, 2026-10-07: every Claude Code Bash call started 15 PreToolUse hooks, about 0.44 CPU-s and a third of all new processes on the Studio across ~65 sessions. Most guards exit at once on a missing trigger word, but each still costs a shell, jq or a Python start.

## What Changes
- `config/coding-agents/hooks/bash-dispatch.py`: one hook that runs the listed Bash hooks in parallel and merges their results the way Claude Code merges parallel hooks. It skips a guard only when the guard's own first check would exit 0 silently and the installed guard is byte-identical to the version that check was copied from (sha256 pins).
- `install-policy.py` folds plain Bash command hooks (type, command, timeout, statusMessage) into `~/.agent-lb/managed/coding-agents/bash-hooks.json` and registers the dispatcher in the first one's place; `--uninstall` restores them there. Hooks with `args`, `if`, `async` or other fields stay registered directly.
- `scripts/bash-dispatch-replay.py` replays transcript and block-case payloads through each live hook and through the dispatcher and compares the outcome.

## Impact
Affected specification: coding-agent-hooks (new). Affected code: config/coding-agents/hooks/bash-dispatch.py, config/coding-agents/install-policy.py, scripts/bash-dispatch-replay.py. Installing it changes every Claude Code session's guard configuration, so the install waits for the owner's yes.
