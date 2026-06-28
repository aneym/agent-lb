## Why

Agent-lb still defaults to a per-account response-create cap of 4. Under
Hermes/OpenClaw-style bursts, that self-imposed local gate can produce
`account_response_create_cap` even when process-wide admission and upstream
accounts still have usable capacity.

## What Changes

- Make account-local response-create admission opt-in by defaulting the cap to
  `0` (unlimited).
- Preserve explicit nonzero response-create cap behavior for operators that want
  a local safety valve.
- Keep response-create leases as routing pressure and analytics signals even
  when the cap is disabled.
- Keep account-local cap rejection diagnostics stable when an explicit cap is
  configured.
- Ensure proxy error-response logs include the normalized error code/message
  and add a startup runtime fingerprint for breakage triage.
- Preserve macOS LaunchAgent runtime configuration when the installer adds
  metrics defaults so restart/reinstall cannot silently switch database,
  dashboard-auth, trusted-proxy, argument, or resource-limit settings.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `proxy-admission-control`: account response-create caps become
  operator-configured rather than default admission limits.
- `responses-api-compat`: response-create leases remain pressure signals, but
  the default path does not reject new work by response-create count.
- `deployment-installation`: macOS service reinstall preserves existing
  LaunchAgent runtime configuration while adding missing metrics defaults.
- `proxy-runtime-observability`: startup and error-response logs carry enough
  safe, low-cardinality context to explain local breakage reports.

## Impact

- Affects proxy account-selection defaults for Responses traffic.
- Reduces local 429s during bursty Hermes/OpenClaw workloads.
- Leaves lease acquisition/release metrics and explicit cap rejection metrics
  intact.
- Hardens the local macOS service installer against LaunchAgent env clobbering.
- Improves incident triage logs without exposing prompts, API keys, account
  tokens, raw database URLs, or request payloads.
