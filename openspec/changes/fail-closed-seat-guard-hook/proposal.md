# Proposal

## Why

The installed `seat-guard` hook could produce no decision when its interpreter failed before its exception handler or when the hook command failed. That turns a guard failure into an admitted tool call.

## What Changes

- Register a fail-closed `seat-guard` PreToolUse command that emits a denial when its Python process exits unsuccessfully.
- Make the guard's emergency denial output independent of Python's `json` module.
- Add regression coverage for shadowed standard-library imports and the installed shell wrapper.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `deployment-installation`: The coding-agent policy installer must register a fail-closed guard command.

## Impact

- `config/coding-agents/install-policy.py`
- `config/coding-agents/seat-guard.py`
- Coding-agent policy installation tests
