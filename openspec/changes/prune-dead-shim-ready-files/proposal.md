## Why

A per-session shim killed before its cleanup leaves a ready file behind. The launcher currently prunes only `cc-*.proxy` files older than a day, leaving Rails desktop sessions' `rails-desktop-*.proxy` files indefinitely and dead Claude Code files for a day.

## What Changes

- Prune launcher-owned `cc-*.proxy` and `rails-desktop-*.proxy` ready files after a short grace period when their named loopback port is dead or invalid.
- Keep the one-day age limit as a fallback, even if the named port is live; leave the shared `desktop.proxy` and all unrelated names untouched.

## Capabilities

### Modified Capabilities

- `runtime-portability`: the launcher cleans up its per-session shim ready files on subsequent launches.

## Impact

- Affects `clients/claude-lb-launch` and its launcher unit test; no server, schema, or install changes.
