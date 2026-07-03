# Tasks

Work lands incrementally on `main` (fork auto-publish); items below convert to
checked boxes as they land. Unchecked checkboxes are reserved for PR-head
gates per repo governance, so not-yet-landed work is tracked as plain bullets.

## 1. Ownership model

- [x] 1.1 Alembic migration: add nullable `owner_instance` to `accounts`
      (`20260702_000000_add_account_owner_instance`, single head on current
      parent). NULL means "owned by the local instance" — semantically
      equivalent to backfilling this instance's id, without requiring instance
      identity at migration time (see design.md).
- [x] 1.2 Gate proactive and on-demand refresh on ownership at the
      `AuthManager.ensure_fresh` choke point (`AccountNotOwnedError`,
      `is_locally_owned`); pulse scheduler skips non-owned accounts.

## 2. Instance API

- [x] 2.1 Authenticated endpoint: export current tokens + expiry for mirrored
      accounts (owner side) — `GET /api/federation/mirror`, gated by
      `AGENT_LB_FEDERATION_TOKEN` bearer auth (403 unset/mismatch); never
      includes refresh tokens (`app/modules/federation/api.py`,
      `service.py`).
- [x] 2.2 Mirror pull loop on non-owner instances with freshness window and
      failure backoff — `FederationMirrorScheduler`
      (`app/modules/federation/scheduler.py`), interval
      `AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS` (default 300s),
      exponential backoff capped at ~30 min; never overwrites locally-owned
      rows.
- [x] 2.3 Checkout/checkin handshake endpoints (release → confirm → assume),
      interruption-safe (no-double-owner invariant) — durable
      `account_transfers` table (`20260703_000000_add_account_transfers`),
      nonce-keyed idempotency on both owner and taker sides. Tests cover
      checkout retry, 409 conflict, confirm idempotency, checkin retry (no
      re-import), and execute-checkout confirm-failure leaving the account
      locally owned with an unconfirmed transfer
      (`tests/unit/test_federation_service.py`,
      `tests/unit/test_federation_mirror.py`,
      `tests/integration/test_federation_api.py`).

## 3. Routing

- [x] 3.1 Exclude expired-mirror accounts from selection on non-owners
      (hard-expiry predicate shared with the ensure_fresh gate; b128ffbd).
- [x] 3.2 Launcher: local endpoint preference ahead of `CLAUDE_LB_BASE_URL`
      (`CLAUDE_LB_LOCAL_URL` / `CLAUDE_LB_LOCAL_PREFER`), with claim-failure
      fallthrough to the next candidate; existing fallback chain preserved.

## 4. Operator surface (pending)

- [x] 4.1 CLI: `clients/agent-lb-federation` status/checkout/checkin against the
      local instance's execute endpoints, preflighting local+peer health
      (built and stub-validated 2026-07-03).
- [x] 4.2 Dashboard: owner-instance mirror badge per account (41ba03b5);
      menubar deferred (separate app, out of repo scope).

## 5. Validation

- [x] 5.3 Single-instance regression: default deployment behavior unchanged
      (ownership-gate unit tests + full unit suite + scratch-DB service boot
      with migration + `/health`, 2026-07-02).
- [x] 5.1 Unit tests: handshake partial-failure matrix incl. concurrent
      checkout race and settled-nonce double-round-trip regression
      (tests/unit/test_federation_*.py, tests/integration/test_federation_api.py);
      mirror freshness/exclusion in tests/unit/test_load_balancer_selection_exclusion.py.
- [x] 5.1b Localhost two-instance live exercise: scripts/two-instance-exercise.sh
      — two real instances (alpha/beta, real aiohttp peer client), mirror pull,
      double checkout→rotate→checkin round trip, 403s; 21/21 assertions,
      re-run clean by the orchestrator 2026-07-03.
- 5.2 (pending) Live two-instance exercise: checkout to laptop, refresh
  offline from owner, checkin, verify studio refreshes cleanly afterward (no
  `refresh_token_reused`). Deferred to stable ground network — do not restart
  the studio service over the relay.
