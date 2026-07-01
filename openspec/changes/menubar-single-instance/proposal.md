## Why

Two AgentLB menubar processes were observed running simultaneously on the MacBook —
one launched from a stale `~/Applications/AgentLB.app` copy and one from the
LaunchAgent-managed repo bundle. Duplicate status items show conflicting account
counts (one instance is always an outdated build), double the polling load, and are
easy to miss because macOS renders two nearly identical icons. Nothing prevented a
second instance from starting.

## What Changes

- `SingleInstanceGuard` (flock on
  `~/Library/Application Support/AgentLB/menubar.lock`) acquired as the first step
  of app startup. The lock is held for the process lifetime and released by the OS
  on exit/crash.
- Acquisition retries briefly (10 × 0.5s) so `launchctl kickstart -k` restarts —
  where the dying and starting instances overlap for a moment — never strand the
  user with no menubar app.
- A second instance that cannot acquire the lock logs to stderr and exits 0
  (deliberate: the LaunchAgent has no KeepAlive; a duplicate exiting is success).

## Impact

- At most one menubar instance per user session per machine, regardless of how many
  bundle copies exist or how they are launched. Client-only change; verified with
  `swift test` plus a live double-launch check on the Studio.
