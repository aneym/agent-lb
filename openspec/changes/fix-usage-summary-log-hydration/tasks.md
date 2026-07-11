# Tasks

## 1. Interim fix

- [x] 1.1 TTL-cache (60s) the log-derived metrics/cost aggregates in
      `UsageService.get_usage_summary`.

## 2. Follow-up (open)

- [ ] 2.1 Replace the once-per-TTL full-window hydration with SQL aggregation
      (extend existing request-log aggregate queries with account filtering),
      with parity tests against the Python computation.
- [ ] 2.2 Retention/pruning job for request_logs and usage/additional-usage
      history tables so row counts stay bounded.

## 3. Validation

- [x] 3.1 ruff clean; usage unit tests (289) and integration tests green
      (one pre-existing unrelated failure in
      test_accounts_list_returns_additional_quotas, fails on clean main too).
- [x] 3.2 Deploy to studio; watchdog unpaused; health stable under menubar
      polling.
