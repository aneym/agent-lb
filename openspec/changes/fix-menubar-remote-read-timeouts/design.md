## Context

The menu-bar API client uses one ephemeral URLSession for dashboard reads with
three-second request and five-second resource deadlines. Those values were
chosen for a loopback service but are also applied when `baseURL` points across
the tailnet. On the affected MacBook, all Studio endpoints returned HTTP 200,
but `/api/usage/summary` took 3.68 seconds and therefore lost a race against the
client deadline.

## Goals / Non-Goals

**Goals:**

- Preserve fast failure detection for loopback services.
- Allow healthy remote reads enough time to complete under ordinary tailnet
  and server-query variance.
- Make endpoint locality and its timeout envelope directly testable.

**Non-Goals:**

- Hide or suppress completed timeout, transport, decoding, or HTTP failures.
- Change polling cadence, server query implementation, or retry-row UI.
- Add per-endpoint retry loops.

## Decisions

### Use a locality-aware read timeout policy

Loopback reads retain the existing 3-second request and 5-second resource
deadlines. Remote reads use a 15-second request and 20-second resource deadline.
This is long enough to cover the observed 3.68-second healthy response with
substantial network variance while remaining bounded. A single longer timeout
for all clients was rejected because it would make a stopped local service feel
unresponsive.

### Keep failure semantics unchanged

The API client will continue mapping a completed URL timeout to
`APIError.timeout`; AppState will continue showing a retry row for that genuine
failure. Only the deadline used to decide when a remote request has failed is
changing.

### Test policy selection without live networking

A small value type will derive the timeout envelope from the resolved base URL.
Focused tests will cover loopback host variants and a tailnet hostname. This
avoids flaky timing tests while pinning the production URLSession configuration.

## Risks / Trade-offs

- **A genuinely stalled remote read remains pending longer** → keep the remote
  deadline bounded at 15/20 seconds and retain visible retry state afterward.
- **A future endpoint becomes slower than the envelope** → endpoint latency can
  be optimized or the centralized policy deliberately revised with tests.

## Migration Plan

Build and test the Swift client, archive the OpenSpec change, push `main`, then
rebuild/relaunch the signed bundle on Studio and MacBook. Verify all MacBook
dashboard endpoints complete within the remote envelope and inspect the live
popover. Roll back by reverting the client commit and rebuilding both bundles.

## Open Questions

None.
