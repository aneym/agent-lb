## Why

On 2026-08-18 a launchd bootout without an immediate re-bootstrap severed every in-flight proxy stream; every active Claude session rendered "Server error mid-response". The audit also found that Anthropic's in-band SSE `error` events on HTTP 200 streams are forwarded verbatim but never reach account health or the request log, and that the runtime sync script reports rsync failures as `rc=0` while silently pinning a possibly stale runtime copy.

## What Changes

- Drain in-flight requests on SIGTERM: the server passes a configurable graceful-shutdown window to uvicorn instead of severing active streams at restart.
- Detect in-band Anthropic SSE `error` events on forwarded streams: log them with request and account identity, record a transient account error, and persist the request log as an error with any collected usage.
- Fix the runtime sync script's failure reporting (real rsync rc), retry once on transient StudioExt failures, and emit an explicit ALERT line when the last-good runtime may be stale.
- Log plist mtime/checksum forensics from the watchdog on the first tick a launchd job goes missing, so an unattributed bootout is traceable.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Add in-band stream error detection feeding account health and request logging.
- `deployment-installation`: Add graceful drain on service shutdown and runtime sync/watchdog failure observability.

## Impact

- Affects the Anthropic proxy stream forwarding path (`app/modules/proxy/anthropic_service.py`).
- Affects the server CLI uvicorn configuration (`app/cli.py`).
- Affects the operational scripts `scripts/sync-runtime.sh` (new canonical copy) and `scripts/watchdog.sh`, deployed to `~/.agent-lb/bin/`.
- Does not change retry/failover semantics: a stream that already emitted bytes is still never retried or spliced.
