## Why

Alex, 2026-09-28: "make 'latest' as a model tag route to the latest so we don't need to update." GPT, Cursor and Devin family aliases already resolve against served-model lists. Claude aliases resolved to Claude Code's harness alias, which the installed Claude Code maps on its own schedule: Claude Code 2.1.284 still maps `sonnet` to claude-sonnet-5 after Sonnet 5.5 shipped.

## What Changes

- The LB serves `GET /api/models/anthropic`: upstream `GET /v1/models` read through one pooled Anthropic account (active accounts, refreshed as usual; rate-limited or quota-exceeded ones with their stored token and no refresh, so listing never changes an account's status; up to three tried), cached an hour with one refresh at a time; a failed refresh serves the last good list marked stale. Only model ids leave the upstream response. The endpoint carries the same dashboard-session dependency as `/api/pools`, which route already reads without a session; where a dashboard password blocks it, route falls back to the pinned id.
- `route resolve` resolves `opus-latest`, `sonnet-latest` and `haiku-latest` to the newest non-retired `claude-<family>-<major>[-<minor>]` on that list (dated snapshots never match), cached a day in route; then the alias's `pinned` id; then the harness alias.
- install-policy writes the resolved Sonnet id to `ANTHROPIC_DEFAULT_SONNET_MODEL`, so `model: sonnet` seats follow a new Sonnet at the next policy sync; on uninstall it removes any `claude-sonnet-N[-N]` pin.
- sol-latest and luna-latest keep resolving from the LB's served list (unchanged).

## Impact

- `app/modules/accounts/{api,service,probes,schemas}.py`, `clients/route`, `config/coding-agents/{routing-table.json,install-policy.py,ROUTING.md}`.
- Box seats resolved by `route seat` now carry exact Claude ids instead of the harness alias.
