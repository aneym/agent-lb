## Why

Agent-lb currently defaults to a per-account active stream cap of 8. Long-lived
Codex/Responses streams can exhaust that arbitrary default even when upstream
accounts are healthy, producing local 429s such as `account_stream_cap`.

## What Changes

- Make active stream concurrency caps opt-in by defaulting the per-account stream
  cap to `0` (unlimited).
- Keep per-account response-create admission enabled by default because it guards
  the short, expensive upstream response creation phase.
- Preserve configured stream-cap behavior for operators that explicitly set a
  nonzero stream cap.
- Keep stream leases as routing pressure and observability signals even when the
  cap is disabled.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `proxy-admission-control`: account stream caps become operator-configured
  rather than default admission limits.
- `responses-api-compat`: active stream leases remain pressure signals, but the
  default path does not reject new work by active stream count.

## Impact

- Affects proxy account selection and admission defaults.
- Reduces local 429s for long-running stream workloads.
- Leaves explicit overload diagnostics intact when a nonzero stream cap is
  configured.
