## Tasks

- [x] Capture per-account exclusion detail and earliest retry time on core balancer selection failures (`SelectionResult.excluded_accounts` / `retry_at`).
- [x] Thread exclusion detail through `LoadBalancer.select_account` to `AccountSelection` for terminal failures.
- [x] Enrich no-accounts error envelopes with `resets_at`, `resets_in_seconds`, and `error.diagnostics` in the HTTP bridge, proxy service, and streaming SSE error events.
- [x] Derive a `Retry-After` header from `error.resets_in_seconds` on logged 429/503 error responses.
- [x] Escalate chronic usage-refresh identity mismatches after 3 consecutive discarded cycles (single ERROR log, `identityMismatch` on the accounts API, cleared on next accepted refresh).
- [x] Add `GET /api/availability` aggregating per-provider availability, unavailable seats with reset times, and degradation state.
- [x] Add regression coverage (`tests/unit/test_no_accounts_diagnostics.py`, `tests/unit/test_availability_endpoint.py`).
- [x] Run focused and full validation gates (pytest, ruff, architecture ratchet).
- [x] Validate OpenSpec change locally.
  - `npx --yes @fission-ai/openspec@latest validate add-no-accounts-diagnostics --strict`
