# Tasks

- [x] Parse `extra_usage` in `app/core/clients/anthropic_usage.py`; thread through the usage refresh into account credits fields (no migration; reuse existing columns)
- [x] `ANTHROPIC_ROUTE_TO_EXTRA_USAGE` setting (default false) in settings.py
- [x] Eligibility gate in `_provider_quota_eligibility`: primary utilization >= 100 blocks the account; pool-exhausted behavior per setting (429 + earliest reset vs last-resort)
- [x] Response header tripwire in the messages path: confirm real header names from a live subscription-covered response, record quota cooldown on exhausted/overage status
- [x] Pool-exhausted wait: `ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED` (default true) + `ANTHROPIC_POOL_EXHAUSTED_WAIT_MAX_SECONDS` (default 21600); streaming requests hold open in the messages body with bounded re-poll + jitter and re-attempt selection after the earliest reset; cap expiry emits the existing mid-stream structured rate-limit error; non-streaming keeps the immediate 429
- [x] Tests: gate excludes credit-billing account when alternatives exist; pool-exhausted returns 429 envelope by default; opt-in last resort; tripwire records cooldown and next request rotates; extra_usage parsing + credits surfacing; wait holds then serves after reset, cap expiry emits structured error, disabled config keeps immediate envelope (injected sleep/clock, no real sleeps)
- [x] `uv run ruff check app tests` + targeted suites pass; openspec validate
