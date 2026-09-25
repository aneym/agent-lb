# Resolve ccgpt models from the served model list

## Why

`CCGPT_MODEL_ALIASES` was a fixed table (`gpt-6-sol`, `gpt-6-luna` and their
`-low/-medium/-high/-xhigh` forms), and the `ccgpt` launcher gate accepted one
pinned model (the installed copy still pinned `gpt-5.6-sol`). Every new GPT
release needed a code change before Claude Code subagents or Workflow agents
could use it.

## What Changes

- `/v1/messages` and `/v1/messages/count_tokens` route a model name through the
  GPT bridge when it resolves against the served model list: any served `gpt-*`
  slug, or `sol-latest` / `luna-latest` (the newest served
  `gpt-<version>-<family>`), each optionally suffixed with a pinned effort.
- `/v1/ccgpt/messages` runs the resolved GPT model; Claude names keep locking to
  the canonical Sol model, and an unresolvable GPT name gets HTTP 400 instead of
  silently running Sol.
- The `ccgpt` launcher defaults to `sol-latest` and its gate admits any GPT name
  (the LB resolves it); Claude names are still refused before upstream.

## Impact

- `app/modules/proxy/api.py`, `clients/claude-lb-launch`.
