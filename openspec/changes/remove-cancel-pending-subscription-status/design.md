## Context

Subscription ledger values are local operator notes, not live vendor billing API
facts. The ledger still needs to track renewal dates, active-until dates,
amounts, verification timestamps, and notes, but status labels should avoid
claiming a vendor-side cancellation state that agent-lb has not verified.

## Decisions

- Keep the status contract small: `active`, `pause_pending`, `paused`, and
  `canceled`.
- Treat `currentPeriodEndAt` as independent metadata. The dashboard labels it
  "Active until" whenever it is present, regardless of status.
- Preserve compatibility for rows already persisted as `cancel_pending` by
  normalizing them to `active` at the account API boundary.
- Do not add a migration for legacy rows in this change. Normalization avoids a
  deployment-time data edit and keeps old rows routable.

## Risks / Trade-offs

- Existing raw database rows may still contain `cancel_pending` until they are
  edited or cleaned up, but `/api/accounts` no longer exposes that value.
- `pause_pending` remains a manual operator-intent status. It should not be used
  to claim vendor billing state unless the notes and verification timestamp make
  that clear.
