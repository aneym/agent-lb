# Tasks

## 1. Ownership model

- [ ] 1.1 Alembic migration: add `owner_instance` to `accounts` (default: this
      instance's id), single head on current parent, backfill existing rows.
- [ ] 1.2 Gate proactive refresh and on-demand refresh on ownership in
      `AuthManager` / scheduler paths.

## 2. Instance API

- [ ] 2.1 Authenticated endpoint: export current tokens + expiry for mirrored
      accounts (owner side).
- [ ] 2.2 Mirror pull loop on non-owner instances with freshness window and
      failure backoff.
- [ ] 2.3 Checkout/checkin handshake endpoints (release → confirm → assume),
      interruption-safe (no-double-owner invariant), with tests for the
      partial-failure paths, not only all-success.

## 3. Routing

- [ ] 3.1 Exclude expired-mirror accounts from selection on non-owners.
- [ ] 3.2 Launcher: optional local endpoint preference ahead of
      `CLAUDE_LB_BASE_URL`, existing fallback chain preserved.

## 4. Operator surface

- [ ] 4.1 CLI/skill: `checkout <accounts> --to <instance>` / `checkin`,
      with clear preflight (connectivity both ways) and status display.
- [ ] 4.2 Dashboard/menubar: show owner instance per account.

## 5. Validation

- [ ] 5.1 Unit tests: ownership gating, mirror freshness, handshake
      interruption matrix.
- [ ] 5.2 Live two-instance exercise: checkout to laptop, refresh offline from
      owner, checkin, verify studio refreshes cleanly afterward (no
      `refresh_token_reused`).
- [ ] 5.3 Single-instance regression: default deployment behavior unchanged.
