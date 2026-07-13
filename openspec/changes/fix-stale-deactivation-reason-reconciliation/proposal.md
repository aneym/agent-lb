# Fix: pulse reconciles stale deactivation reasons on active accounts

## Why

An account can end up `active` while still carrying a stored
`deactivation_reason` (observed live: alex@kineticapps.io was `active`, probed
HTTP 200, yet kept "Authentication failed: invalid_api_key"). Balancer recovery
transitions (rate-limit/quota re-admission) restore status without touching the
reason, and concurrent reauth/persist races can strand one. Clients treat a
non-null `deactivationReason` as disconnected — the macOS menubar rendered the
account as "re-auth needed" although auth was healthy.

## What Changes

- The account pulse HEALTHY verdict now also clears a stored
  `deactivation_reason` when the account is already `active`, emitting an
  `account_pulse_cleared_stale_deactivation_reason` audit action.
- Regression test: `test_pulse_clears_stale_deactivation_reason_on_active_account`.

## Impact

- Affected specs: `account-routing` (account pulse reconciliation).
- Affected code: `app/modules/accounts/pulse.py`.
- Menubar/dashboard stop rendering healthy accounts as disconnected within one
  pulse cycle; `POST /api/accounts/{id}/reactivate` remains the immediate manual
  remediation.
