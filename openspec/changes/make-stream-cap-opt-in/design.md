## Overview

The existing account-local stream cap was introduced alongside response-create
admission as part of a broad concurrency stabilization pass. In practice, the
two controls behave differently:

- Response-create leases are short lived and protect the upstream creation phase.
- Stream leases are long lived and can span full Codex turns or bridge sessions.

Keeping a default cap on long-lived streams turns normal active work into a
fixed pool-wide ceiling. With four eligible accounts and the previous default of
8, the pool could reject new stream work at roughly 32 active streams even
though the process-level admission lanes and upstream accounts were otherwise
available.

## Decision

Default `proxy_account_stream_limit` to `0`, where `0` means unlimited. Continue
to acquire stream leases so routing can still see active stream pressure,
spread new work, estimate leased token pressure, release leases on terminal
paths, and report metrics. Only a nonzero operator configuration should make
stream count an eligibility cap.

The per-account response-create cap remains enabled by default because it limits
only the expensive startup phase and is released once upstream accepts or rejects
the response.

## Failure Modes

- If an operator configures a nonzero stream cap, every saturated eligible
  account still returns the stable local `account_stream_cap` reason.
- If stream leases are orphaned, the existing stale-lease watchdog still reclaims
  them; with the default cap disabled, stale stream leases no longer block new
  work solely by count, but they still affect pressure scoring until reclaimed.
