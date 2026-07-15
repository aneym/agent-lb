## Why

Agent-lb startup latency is currently opaque and sometimes dominated by avoidable synchronous work: full database schema-drift comparison can delay service readiness, while the Claude launchers pay redundant probes and a second full Python launcher startup. Operators need a repeatable baseline and low-cardinality phase telemetry so regressions can be found and improved over time instead of inferred from wall-clock anecdotes.

## What Changes

- Add a startup-performance contract spanning service boot, macOS service restart, and the `cc`/`ccgpt`/`co` launcher paths.
- Add a repeatable benchmark command that records spawn, startup-probe, readiness-probe, and useful-output timing as machine-readable history with comparison thresholds.
- Emit structured service-startup phase summaries and Prometheus startup metrics with bounded, non-sensitive labels.
- Keep full schema-drift validation available to explicit database checks while removing it from the default readiness-critical path after migration state is confirmed current.
- Remove redundant launcher probes and defer interactive account selection until first use without weakening headless reset handling, fail-closed compatibility checks, or proxy readiness proof.
- Make the macOS service installer wait on actual process/ready state rather than fixed cooldown time, and verify `/health/ready` before success.

## Capabilities

### New Capabilities

- `startup-performance`: Defines measurable service and launcher timing boundaries, repeatable benchmark history, default database validation posture, and regression reporting.

### Modified Capabilities

- `proxy-runtime-observability`: Adds structured startup-phase and readiness-duration telemetry with low-cardinality, secret-safe metrics and logs.
- `deployment-installation`: Makes macOS service restart completion readiness-driven and reports its measured timing.

## Impact

Affected areas include `app/main.py`, database startup checks, Prometheus instrumentation, health/startup reporting, `clients/claude-lb-launch`, `clients/ccdex`, the macOS installer, benchmark tooling, tests, and OpenSpec context. No public proxy request schema or upstream compatibility contract is intentionally broken.
