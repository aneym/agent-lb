# Tasks

## 1. Interim fix

- [x] 1.1 TTL-cache (60s) the log-derived metrics/cost aggregates in
      `UsageService.get_usage_summary`.

## 2. Follow-up

- [x] 2.1 (no-provider path) Serve metrics/cost from the existing SQL
      aggregates (`aggregate_activity_since`, `top_error_since`,
      `aggregate_by_bucket`) — no row hydration. Loop-lag telemetry showed the
      TTL cache alone still froze the loop up to 62s per recomputation.
- [ ] 2.1b Provider-scoped path still hydrates once per TTL; replace with
      account-filtered SQL aggregates with parity tests against the Python
      computation (dispatched).
- [ ] 2.2 Retention/pruning job for request_logs and usage/additional-usage
      history tables so row counts stay bounded.

## 3. Validation

- [x] 3.1 ruff clean; usage unit tests (289) and integration tests green
      (one pre-existing unrelated failure in
      test_accounts_list_returns_additional_quotas, fails on clean main too).
- [x] 3.2 Deploy to studio; watchdog unpaused; health stable under menubar
      polling.
