# Add launcher startup grace for cold local boots

## Why

After a machine reboot, the local agent-lb process can be loaded in launchd but
need tens of seconds to import and become ready. The interactive Claude launcher
currently gives its local readiness probe only 350 ms, so it immediately falls
back while the intended local service is actively starting. Startup telemetry
also reports a process-start total without separating time outside the named
startup phases.

## What Changes

- When the local readiness probe fails because the endpoint is unreachable,
  wait for a loaded agent-lb launchd job to become ready within a configurable
  startup grace period.
- Preserve the current fast path, remote-candidate behavior, and zero-grace
  opt-out.
- Report startup time not attributed to named phases as a separate summary-log
  field without changing the phases JSON object.

## Impact

- Affected specs: `startup-performance`, `proxy-runtime-observability`
- Affected code: `clients/claude-lb-launch`, `app/core/startup.py`
- Operators get bounded cold-boot waiting for the local launcher candidate and
  explicit visibility into pre-phase/import startup cost.
