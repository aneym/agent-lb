# Tasks

## 1. Server stall fixes

- [x] 1.1 Upgrade anyio to 4.14.2 (`uv lock --upgrade-package anyio && uv sync`).
- [x] 1.2 Throttle the http-bridge idle prune scan to a 5s minimum interval.

## 2. Stall self-documentation

- [x] 2.1 Add `dump_all_thread_stacks` (timestamped, best-effort) to `app/core/forensics`.
- [x] 2.2 Add the stall watchdog thread to `EventLoopLagMonitor` (≥5s stale heartbeat → dump, 60s rate limit).

## 3. Client and front resilience

- [x] 3.1 `cc` interactive probe: 3 retries with 1s gap against `/health/ready`.
- [x] 3.2 Front proxy log writes survive ENOSPC/EPIPE.

## 4. Round 2: residual-lag fixes

- [x] 4.1 Share one upstream-websocket `SSLContext`; stop building one per connect on the loop.
- [x] 4.2 Disable permessage-deflate on upstream websocket connects.
- [x] 4.3 Raise `cc` local probe timeout default to 1.5s.
- [x] 4.4 Raise `cc` ready-probe timeout defaults (local + remote) to 3.0s
      after host-overload probes timed out against a live LB (2026-07-17).
- [x] 4.4 Extend websocket transport kwargs regression test (compression/ssl).

## 5. Validation and rollout

- [x] 4.1 `ruff check app clients`, launcher `py_compile`, `node --check` on the front script.
- [x] 4.2 Focused pytest on http-bridge/health/resilience suites.
- [x] 4.3 Restart `com.aneyman.agent-lb`, confirm `/health/ready` under live load and a `cc` round trip; watch lag warnings subside.
