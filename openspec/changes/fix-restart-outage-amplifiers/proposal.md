# Fix restart-outage amplifiers (watchdog boot grace, installer re-bootstrap, log rotation)

## Why

On 2026-07-11 every long-lived client chat (Claude Code and Codex) eventually
died with `502 Bad Gateway` from the tailnet proxy in front of the live service.
Diagnosis on studio showed the service restarted 8+ times that day and that each
restart is a 1–2.5 minute hard outage, amplified by three defects:

1. **Watchdog kills booting instances.** Cold boot to first accepted request
   takes 60–80s under host load, but `KICK_GRACE_SECONDS` defaulted to 60 and
   the kick path never checked the service process age. Observed at
   2026-07-11T20:24:50Z: a process died on its own, launchd's replacement spent
   ~60s booting, and the watchdog `kickstart -k`-ed it ~4 seconds after it
   became ready — doubling the outage.
2. **`install-service.sh` can leave the job booted out.** After
   `launchctl bootout` it waits up to 30s for localhost:2455 to free and then
   `exit 1`s with nothing bootstrapped when the old process is still draining
   long streams — the exact shape of the morning's 10-minute outage, now only
   partially mitigated by the watchdog revive (still a guaranteed ≥60s hole).
3. **Unbounded service logs.** `~/.agent-lb/agent-lb.err.log` reached 446MB on
   a 97%-full disk; nothing rotates the launchd-owned stdout/stderr files.

## What Changes

- `scripts/watchdog.sh`:
  - `AGENT_LB_WATCHDOG_KICK_GRACE` default 60 → 240 seconds.
  - New boot-grace guard: before kickstarting, resolve the job's current pid
    and skip the kick (keeping the unhealthy counter) while the process is
    younger than `AGENT_LB_WATCHDOG_BOOT_GRACE` (default 240s). A hung old
    process still gets kicked; a booting replacement no longer does.
  - New size-gated rotation of the launchd service logs
    (`AGENT_LB_WATCHDOG_SERVICE_LOG_MAX_MB`, default 256): copy → truncate in
    place → gzip the copy. Never `mv` (launchd's append fd would keep writing
    to the renamed inode until the next restart).
- `scripts/install-service.sh`: after booting out the existing job, a
  still-busy port no longer aborts the install. It warns and proceeds to
  bootstrap — KeepAlive retries the bind until the draining process exits.
  The wait window becomes configurable
  (`AGENT_LB_INSTALL_PORT_FREE_TIMEOUT_SECONDS`, default 30).
- **Always-up TCP front** (`scripts/agent-lb-front.mjs` +
  `scripts/install-front.sh`, LaunchAgent `com.aneyman.agent-lb-front`): owns
  127.0.0.1:2455 permanently and pipes raw TCP to the app on 127.0.0.1:2457.
  While the app restarts, new connections are held and the upstream connect is
  retried (default up to 180s) instead of refused — tailscale serve and local
  clients stop seeing `502`/connection-refused during deploys. The app's
  launchd plist moves to `--port 2457`; nothing else changes (clients,
  tailscale serve, watchdog, and the dashboard keep targeting 2455).

## Impact

- Affected specs: `deployment-installation`, `deployment-networking`
- Affected code: `scripts/watchdog.sh`, `scripts/install-service.sh`,
  `scripts/agent-lb-front.mjs` (new), `scripts/install-front.sh` (new),
  `tests/unit/test_watchdog_script.py`, `tests/unit/test_install_service.py`
- Operator contract: restarts stop being client-visible 502 windows (new
  requests queue at the front; in-flight streams still cut once and rely on
  client retry/reconnect). A single failure can no longer cascade into a
  double or unbounded outage, and service logs stop growing without bound.
- Live topology change on studio: app listens on 127.0.0.1:2457; the front
  owns 127.0.0.1:2455.
