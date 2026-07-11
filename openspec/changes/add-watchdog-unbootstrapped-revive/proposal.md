# Add watchdog revival of un-bootstrapped launchd service

## Why

On 2026-07-11 the live `com.aneyman.agent-lb` service on studio was down for ~10
minutes: a deploy booted the job out of launchd, rewrote the plist, and never
bootstrapped it back. The watchdog (`~/.agent-lb/bin/watchdog.sh`, previously
unversioned) explicitly treated "job not bootstrapped" as "operator intentionally
stopped it" and refused to revive, so nothing recovered the service until a human
intervened. The watchdog script also had no canonical source in the repo, so its
behavior could drift per-host and could not be reviewed or tested.

## What Changes

- Add `scripts/watchdog.sh` as the versioned canonical source for the host
  watchdog (deployed to `~/.agent-lb/bin/watchdog.sh`).
- Change the un-bootstrapped semantics: the pause file
  (`~/.agent-lb/watchdog.pause`) is now the only intentional-stop signal. If the
  job is missing from launchd, the plist exists, and no pause file is present,
  the watchdog re-bootstraps the service after 2 consecutive ticks (~60s) —
  a grace window so it does not race a deploy that bootstraps right back.
- Persist a `missing` counter in the watchdog state file alongside the existing
  unhealthy `count` and `last_kick`.
- Add unit coverage exercising the script with `launchctl`/`curl` shims.

## Impact

- Affected specs: `deployment-installation`
- Affected code: `scripts/watchdog.sh` (new), `tests/unit/test_watchdog_script.py` (new)
- Operator contract change: intentional downtime now requires touching the pause
  file; simply booting the job out no longer keeps it down.
