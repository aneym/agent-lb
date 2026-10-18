# Anthropic quota swap

## Why
Quota exhaustion held new authority dispatches and delayed merges.

## What Changes
Use the existing Anthropic pool remaining fraction and recent limit errors to switch new authority dispatches to the lead-specified Codex seats. Reuse the reserve threshold; recover only above reserve plus ten points and after fifteen minutes without a limit error. Preserve explicit flags and existing jobs. Persist the mode, log flips and expose `route quota-mode` and class resolution.

## Impact
Affected specification: routing-pools. Affected code: clients/route, config/coding-agents/routing-table.json. Implementation and exploration remain unchanged.
