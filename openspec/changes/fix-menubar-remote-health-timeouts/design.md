## Context

`APIClient` already chooses local or remote timeout envelopes for dashboard reads, but constructs `healthSession` with the local policy unconditionally. On the MacBook, a cold Tailnet TLS handshake plus `/health` response took 6.59 seconds; the 3-second request / 5-second resource envelope classified the healthy remote service as unreachable. Warm probes completed in 1.45 seconds and 0.34 seconds, confirming a cold-connection deadline mismatch rather than service unavailability. Separately, healthy loopback dashboard reads after disk recovery measured 8.99 seconds for cold `/api/accounts`, 5.94 seconds for `/api/usage/summary`, and up to 4.96 seconds for request logs, so the current 3/5 loopback dashboard envelope also rejects healthy responses.

## Goals / Non-Goals

**Goals:**

- Keep loopback health checks fast-failing at 3 seconds request / 5 seconds resource.
- Accept a healthy cold remote health response within a bounded deadline.
- Accept healthy dashboard reads within a bounded 15-second request / 20-second resource deadline locally and remotely.
- Keep health and dashboard timeout selection as pure, directly testable policies.

**Non-Goals:**

- Retrying health probes or changing service-status state transitions.
- Changing dashboard-read or account endpoint behavior.
- Masking genuine remote health failures beyond the bounded deadline.

## Decisions

1. **Use separate health and dashboard policy types.** Health remains 3/5 on loopback and uses 15/20 remotely. Dashboard reads use 15/20 regardless of locality. Health therefore retains fast local liveness detection, while database-backed reads have a simple contract with substantial headroom over the observed 8.99-second healthy cold query. A 10/15 local dashboard envelope was rejected because its request deadline left only about one second of headroom and risked another threshold-edge regression.

2. **Reuse the remote dashboard envelope for remote health probes.** The shared 15/20 remote values cover the observed 6.59-second cold Tailnet path and avoid an arbitrary third remote envelope. A separate 10/15 remote-health policy was considered, but it leaves less cold-network headroom without improving loopback failure detection.

3. **Test policy values and selection, not the network.** Focused unit tests cover the distinct local health, local dashboard, remote health, and remote dashboard contracts deterministically. Live endpoint timing remains deployment verification rather than a unit-test dependency.

## Risks / Trade-offs

- [Risk] A dead remote health probe can now delay unreachable status for up to the bounded remote envelope. → Mitigation: the limit remains finite, and avoiding false unreachable state during normal cold TLS is more important than a 3-second remote failure signal.
- [Risk] Local dashboard failures can take longer to surface. → Mitigation: the 15/20 envelope remains bounded; `/health` independently retains fast local liveness detection at 3/5.
