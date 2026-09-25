# Fix ccgpt first-byte hang

## Why

Claude Code sessions and subagents on GPT aliases (`gpt-6-sol-*`, `gpt-6-luna-*`)
answered their first turn and then hung: no response headers for 181 s, a
client abort, and retries that hung the same way. Through the desktop MITM
proxy Claude Code sees the first-party host and turns on its message-threads
beta. Turn 1 carries `thread: create`; every later turn carries
`thread: continue` with only the delta (tool result, new reminders), no system
prompt beyond the billing line, and no tools, expecting the server to hold the
rest. The bridge ignored `thread`, translated the delta into an orphan
`function_call_output` with no tools or history, and upstream accepted it and
never produced an event. The startup peek then held the headers indefinitely.

## What Changes

- The ccgpt route refuses any request carrying `thread` with HTTP 400 and
  `error.details.error_code = thread_unsupported_request`. Claude Code reads
  that code, resends the turn stateless and keeps the model stateless for the
  session.
- The startup peek is bounded: no frame within 90 s returns an Anthropic
  `api_error` and closes upstream; once `message_start` is buffered, headers go
  out after a 10 s grace even if content has not started.
- `scripts/ccgpt_e2e_eval.py` runs real Claude Code sessions (Sol, Luna, and a
  Claude driver with a Sol subagent) through `claude-lb-launch` and fails on a
  hang or an incomplete tool run.

## Impact

- Affected specs: `claude-harness-codex`.
- Affected code: `app/modules/proxy/api.py`, `app/modules/proxy/claude_codex_bridge.py`.
