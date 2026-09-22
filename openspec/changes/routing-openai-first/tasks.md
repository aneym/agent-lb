## 1. Implementation
- [x] Fail-close Anthropic-model Agent seats against the limit-watch snapshot while preserving non-Anthropic forwarders, including malformed route-table and top-level setup failures.
- [x] Restore unconditional Fable-pin denial and fail-close unknown seats and malformed hook payloads.
- [x] Track and update routing sources and installer mapping.
- [x] Enforce account-level admission and freshness, including provider-specific unavailable windows and advisory verifier fallback.
- [x] Add typed Jev classification and dispatch ledger output.
- [x] Align policy and telemetry hooks.

## 2. Verification
- [x] Run at most eight offline regression tests and source checks.
- [x] Validate OpenSpec strictly.
- [x] Commit and push the fork PR branch; leave installation to the driver.
