## 1. Regression Coverage

- [x] 1.1 Add focused cancellation-classification tests for direct, URL, wrapped transport, timeout, and HTTP failure cases.

## 2. Section Error Semantics

- [x] 2.1 Ignore cancellation in foreground pool, accounts, and recent fetch error handling.
- [x] 2.2 Clear prior section errors after successful silent background fetches without making silent failures alerting.

## 3. Validation and Rollout

- [x] 3.1 Run focused and full Swift tests plus strict OpenSpec validation.
- [x] 3.2 Rebuild/relaunch the signed menu-bar app and verify the section-state regression path; direct visual inspection was unavailable because the Mac session was locked.
