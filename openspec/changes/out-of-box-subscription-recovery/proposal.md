## Why

The account pulse runs a full probe pass every `account_pulse_interval_seconds`
(default 6h). When an operator fixes an account upstream — resubscribes an
account whose subscription ledger is `canceled`, or re-authenticates a
`reauth_required`/`deactivated` account — agent-lb takes up to a full interval
to notice and restore routing. Today the operator has to manually run
`POST /api/accounts/{id}/subscription/check` to shorten that window. Recovery
should work out of the box within minutes.

## What Changes

- New setting `account_pulse_recovery_interval_seconds` (default 900, `gt=0`,
  env `ACCOUNT_PULSE_RECOVERY_INTERVAL_SECONDS`) next to the other
  `account_pulse_*` settings.
- `AccountPulseScheduler` keeps the existing full pass on `interval_seconds`
  and adds a fast recovery lane: the run loop wakes at
  `min(interval_seconds, recovery_interval_seconds)`; each wake runs a full
  pass when a full interval has elapsed since the last one, otherwise a
  recovery pass that probes only recovery-pending accounts (not `paused`, and
  subscription ledger `canceled` or status `reauth_required`/`deactivated`).
  Recovery passes reuse the existing probe/verdict machinery, so restoration
  flows through the existing HEALTHY handling (ledger back to `active`, status
  reactivation, selection-cache invalidation, `account_pulse_*` audit events).
- Backoff interaction (documented decision):
  - `_record_failure` is only invoked for transient/unclassified refresh
    failures and INCONCLUSIVE probe verdicts. An UNSUBSCRIBED verdict on a
    still-canceled account clears the failure entry (unchanged), so the
    recovery lane re-probes it once per recovery wake — the wake cadence
    itself bounds upstream probes to roughly one per recovery interval.
  - INCONCLUSIVE failures still escalate the existing exponential backoff
    (base 600s, max 21600s) for full passes, but the recovery lane caps the
    *effective* delay for recovery-pending accounts at
    `recovery_interval_seconds`, so backoff can never push recovery detection
    past the full interval.
- If `recovery_interval_seconds >= interval_seconds`, the recovery lane
  degrades to the original single-cadence behavior.

## Capabilities

### New Capabilities

### Modified Capabilities

- `usage-refresh-policy`: the account pulse gains a fast recovery lane so
  upstream-fixed accounts re-enter the routable pool within
  `account_pulse_recovery_interval_seconds` instead of one full pulse interval.

## Impact

- Affects `app/modules/accounts/pulse.py` and `app/core/config/settings.py`;
  no DB schema changes and no new endpoints.
- Cost: recovery passes probe only recovery-pending accounts, so a healthy
  pool adds zero upstream traffic between full passes; a still-broken account
  costs one minimal probe per recovery interval (~4 output tokens on
  Anthropic).
- Multi-replica gating is unchanged: both lanes run behind the same leader
  election and shared per-scheduler lock.
