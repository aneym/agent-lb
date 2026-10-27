# Install routing audit siblings during runtime upgrades

## Why

The audit was split into sibling modules, but runtime-follow only refreshes bin files that already exist. An installation made before the split therefore upgrades the entrypoint without installing its imports, and the next scheduled audit fails during module import.

## What Changes

- Keep the routing audit entrypoint and required sibling modules in one manifest shared by the installer and runtime-follow.
- Treat missing required siblings as outdated files eligible for installation when their entrypoint is already installed.
- Preserve existing drift handling, install siblings before the entrypoint, and record originally absent destinations so restart rollback removes newly created files.
- Exercise the real runtime-follow upgrade from the historical pre-split audit and run the upgraded CLI.

## Impact

- Affected code: `scripts/runtime_bin.py`, `scripts/install_routing_audit.py`, and the routing audit manifest.
- Existing pre-split installations converge on a runnable split audit without a manual reinstall.
