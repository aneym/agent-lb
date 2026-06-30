## Why

Context-compaction calls (a single large ~150–200k-token `/v1/responses`
streaming request issued by the Hermes client to shrink an over-long session)
were intermittently failing against the codex pool with HTTP 503 `no_accounts`
("all upstream accounts are unavailable") even though 4 codex accounts were
ACTIVE with quota remaining. Observed in production: bursts of ~130 gpt-5.5
`no_accounts`/hour, each logged with `excluded_count=0` — i.e. the routable pool
was emptied _before_ any health/cooldown filter, invisible to the dashboard
ACTIVE status.

Root cause: a large compaction payload's connect + serialize + send can consume
the 60s first-event window, so the upstream produces zero events within it and
the bridge raises `bridge_first_event_timeout`. That timeout was treated as an
**account-attributable** transient error and recorded against the account's
in-memory `error_count`. A handful of such timeouts pushes every codex account
past the `error_count >= 3` backoff threshold (`app/core/balancer/logic.py`),
excluding them from selection for 30–300s and collapsing the pool to a spurious
`no_accounts` 503 that breaks compaction for **all** callers. The compaction
failover loop only tried **2** accounts (below a typical pool), so it also gave
up while other healthy accounts were never attempted.

A first-event timeout means the upstream answered nothing — almost always
upstream slowness or a silently-dropped `response.create`, not a fault of the
account's credentials or quota. Penalizing the account violates the documented
invariant that idle/no-event disconnects must not mark an otherwise-healthy
account unhealthy.

## What Changes

- Classify `bridge_first_event_timeout` as an **account-neutral** error code so
  it no longer records an account-health error (`_handle_stream_error` returns
  before `record_error`). The existing per-bridge-key cooldown + sticky rebind
  still steer the retry to a different account, so failover is unaffected; only
  the global account-health penalty is removed.
- Raise the compaction account-failover attempt budget
  (`_COMPACT_MAX_ACCOUNT_ATTEMPTS`) from 2 to 4 so a single critical compaction
  call fails over across a typical codex pool before returning `no_accounts`.

## Impact

- Affected capability: `responses-api-compat` (account-health classification of
  streaming failures; compaction failover breadth).
- A persistently-broken account that only ever first-event-times-out is no
  longer demoted via this path; the per-bridge-key cooldown still excludes it
  per conversation, and genuine account faults (connection/5xx/auth) still
  demote it. This is the documented, intended tradeoff.
- No API/schema change. Behavior change only in account-health accounting and
  compaction failover breadth.
