## 1. Delivery
- [x] 1.1 Create localized broker/client patch and checked apply script.
- [x] 1.2 Document base version, env vars, post-update application, and limitations.

## 2. Verification
- [x] 2.1 Run real-process Test A (no connections) and Test B (connected past idle), reconnect, shutdown, and TERM-resistant descendant coverage.
- [x] 2.2 Verify idempotent application and atomic upstream-drift rejection.
- [ ] 2.3 Validate all OpenSpec specs and relevant syntax/tests (change strict validation and syntax pass; global specs have existing failures).
- [ ] 2.4 Apply live patch and record actual installed diff, or explicit permission blocker.
- [ ] 2.5 Commit agent-lb branch; run required exact-SHA gate before merge.
