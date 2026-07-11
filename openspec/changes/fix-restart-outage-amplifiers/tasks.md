# Tasks

## 1. Watchdog script

- [x] 1.1 Raise `AGENT_LB_WATCHDOG_KICK_GRACE` default to 240s
- [x] 1.2 Add boot-grace guard: skip kickstart while the service pid is younger
      than `AGENT_LB_WATCHDOG_BOOT_GRACE` (default 240s); keep counting
- [x] 1.3 Add size-gated service-log rotation (copy → truncate → gzip)

## 2. Installer

- [x] 2.1 Port still busy after the post-bootout wait: warn and bootstrap
      anyway instead of exiting with the job unloaded; make the wait window
      configurable via `AGENT_LB_INSTALL_PORT_FREE_TIMEOUT_SECONDS`

## 3. Always-up TCP front

- [x] 3.1 Add `scripts/agent-lb-front.mjs`: hold-and-retry TCP pipe
      127.0.0.1:2455 → 127.0.0.1:2457, zero deps
- [x] 3.2 Add `scripts/install-front.sh` (LaunchAgent
      `com.aneyman.agent-lb-front`, KeepAlive)
- [x] 3.3 Local smoke: connection held while upstream down completes when
      upstream appears; pass-through 200; no instant refusal during outage

## 4. Tests

- [x] 4.1 Watchdog: kick skipped within boot grace; kick fires when older;
      oversized log rotated and truncated
- [x] 4.2 Installer: busy-port path proceeds to bootstrap (shimmed
      launchctl/lsof/curl, temp HOME)

## 5. Deployment & validation (studio)

- [x] 5.1 Unit suites pass (`test_watchdog_script.py`, `test_install_service.py`)
- [x] 5.2 Deploy `scripts/watchdog.sh` to `~/.agent-lb/bin/watchdog.sh`, run a
      manual tick: healthy, oversized err.log rotated
- [x] 5.3 Cutover: pause watchdog → move app plist to `--port 2457` →
      bootstrap front on 2455 → app healthy on 2457 → `/health` 200 via 2455 →
      unpause watchdog
- [x] 5.4 End-to-end: `/backend-api/codex/models` 200 via
      `https://studio.tailf266ac.ts.net:2455` after cutover; restart drill
      shows held connection completing instead of 502
- [x] 5.5 Fast-forward the studio checkout to origin/main
