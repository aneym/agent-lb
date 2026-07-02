# Make Launcher Session-Route Claim Timeout Tunable

## Why

The launcher's sticky session-route claim (`POST /api/anthropic/session-route`)
used a hardcoded 5.0s timeout. Over a DERP-relayed Tailscale path (e.g. plane
wifi that blocks direct connections, ~2.5s RTT), the TLS handshake alone takes
~5s, so the claim flakes and the launcher falls back to plain claude even
though the LB is healthy and the health probe (already tunable via
`CLAUDE_LB_HEALTH_TIMEOUT`) passed. Observed live on 2026-07-02: health probe
succeeded at 15s tolerance, then the claim failed with
`_ssl.c:1063: The handshake operation timed out`.

## What Changes

- The claim request timeout becomes tunable via `CLAUDE_LB_CLAIM_TIMEOUT`
  (float seconds), defaulting to the previous hardcoded 5.0s, matching the
  existing `CLAUDE_LB_HEALTH_TIMEOUT` / `CLAUDE_LB_BANNER_TIMEOUT` pattern.

## Impact

- Affected specs: `account-routing` (launcher client behavior)
- Affected code: `clients/claude-lb-launch` (one line)
- No server, schema, or API changes. Default behavior is unchanged.
