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

## 2. Instance API (pending)

- 2.1 Authenticated endpoint: export current tokens + expiry for mirrored
  accounts (owner side).
- 2.2 Mirror pull loop on non-owner instances with freshness window and
  failure backoff.
- 2.3 Checkout/checkin handshake endpoints (release → confirm → assume),
  interruption-safe (no-double-owner invariant), with tests for the
  partial-failure paths, not only all-success.

## 3. Routing

- 3.1 (pending) Exclude expired-mirror accounts from selection on non-owners.
- [x] 3.2 Launcher: local endpoint preference ahead of `CLAUDE_LB_BASE_URL`
      (`CLAUDE_LB_LOCAL_URL` / `CLAUDE_LB_LOCAL_PREFER`), with claim-failure
      fallthrough to the next candidate; existing fallback chain preserved.

## 4. Operator surface (pending)

- 4.1 CLI/skill: `checkout <accounts> --to <instance>` / `checkin`,
  with clear preflight (connectivity both ways) and status display.
- 4.2 Dashboard/menubar: show owner instance per account.

## 5. Validation

- [x] 5.3 Single-instance regression: default deployment behavior unchanged
      (ownership-gate unit tests + full unit suite + scratch-DB service boot
      with migration + `/health`, 2026-07-02).
- 5.1 (pending) Unit tests: mirror freshness, handshake interruption matrix
  (ownership gating covered by `tests/unit/test_account_ownership_gate.py`).
- 5.2 (pending) Live two-instance exercise: checkout to laptop, refresh
  offline from owner, checkin, verify studio refreshes cleanly afterward (no
  `refresh_token_reused`). Deferred to stable ground network — do not restart
  the studio service over the relay.
