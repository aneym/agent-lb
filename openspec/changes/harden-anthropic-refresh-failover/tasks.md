## 1. Implementation

- [x] 1.1 Canonicalize recognized prefixed refresh-auth failure codes before permanent-failure classification and status selection.
- [x] 1.2 Canonicalize known permanent refresh codes inside `RefreshError` construction.
- [x] 1.3 Make Anthropic message routing continue to another eligible account when selected-account token refresh fails before upstream I/O.
- [x] 1.4 Release API-key usage reservations when streaming Anthropic proxy errors are emitted as SSE error events.

## 2. Verification

- [x] 2.1 Add regression tests for refresh failure classification, Anthropic OAuth `invalid_grant`, balancer status/reason handling, Auth Guardian cache invalidation, Anthropic `/v1/messages` failover, and streaming error cleanup.
- [x] 2.2 Run OpenSpec validation and the relevant automated tests.
- [x] 2.3 Restart the live agent-lb service and smoke Anthropic message routing.
- [x] 2.4 Audit Anthropic account rows and reauthenticate or identify subscription/auth blockers account by account.
