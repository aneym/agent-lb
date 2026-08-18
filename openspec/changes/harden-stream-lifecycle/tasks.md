## 1. Stream error observability

- [x] 1.1 Extend the SSE chunk collector to surface in-band `error` events alongside usage.
- [x] 1.2 On a detected stream error: log with request/account identity, record a transient account error, persist the request log as an error (with usage), and settle the API-key reservation.
- [x] 1.3 Unit tests for the collector (error detection across chunk boundaries; no-error path).
- [x] 1.4 Integration regression at `/v1/messages`: bytes forwarded verbatim, request log records the error.

## 2. Graceful drain

- [x] 2.1 Add `--timeout-graceful-shutdown` / `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN` (default 75s) and pass it to uvicorn.
- [x] 2.2 Raise launchd `ExitTimeOut` above the drain window on the live service plist.

## 3. Ops script hygiene

- [x] 3.1 Fix sync-runtime rsync rc reporting; add one retry and an ALERT line on persistent failure; add the canonical copy to `scripts/`.
- [x] 3.2 Watchdog: log plist mtime/sha forensics on first missing-job tick.
- [x] 3.3 Deploy updated scripts to `~/.agent-lb/bin/` and re-verify service health.

## 4. Validation

- [x] 4.1 `ruff check app clients` passes; affected unit + integration tests pass.
- [x] 4.2 Strict OpenSpec validation passes.
- [x] 4.3 Live service restarted on the new code; `/health` 200 and a streamed request exercised.
