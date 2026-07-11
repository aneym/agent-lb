# Tasks

## 1. Watchdog script

- [x] 1.1 Add `scripts/watchdog.sh` (versioned canonical source) with revival of
      un-bootstrapped service after 2 consecutive ticks, gated on the pause file
      and plist presence
- [x] 1.2 Persist `missing` counter in the watchdog state file

## 2. Tests

- [x] 2.1 Add `tests/unit/test_watchdog_script.py` with launchctl/curl shims
      covering: no revive on first tick, revive on second tick, healthy reset,
      pause suppression

## 3. Deployment & validation

- [x] 3.1 Deploy `scripts/watchdog.sh` to `~/.agent-lb/bin/watchdog.sh` on studio
- [x] 3.2 Live validation on studio: bootout the service, confirm the watchdog
      re-bootstraps it and `/health` returns 200
