# Add federation usage attribution

## Why

A federated pool routes requests from multiple machines (studio owner, macbook
follower, future partner followers), but every instance's `request_logs` are
local to its own SQLite store. The pool owner has no way to answer "who is
doing what on my accounts": usage generated on a follower is invisible on the
owner. With additional people joining the pool, per-instance attribution is
required for capacity planning and accountability. The instance id is already
a durable machine/person identity in the federation model — no new user entity
is needed.

## What Changes

- Followers push idempotent per-day usage rollups (keyed by
  `instance_id + account_id + UTC day`) to the owner over the existing
  federation peer channel, after each successful mirror pull.
- The owner accepts rollups at `POST /api/federation/usage-report`
  (federation Bearer auth, same router as `/mirror`) and stores them in a new
  `federation_usage_daily` table (Alembic migration).
- Any instance exposes `GET /api/usage/instances` (dashboard auth): its own
  live local rollup under its own instance id, merged with any stored reports
  it has received. On the owner this yields the complete pool view.

## Impact

- Affected specs: `instance-federation`
- Affected code: `app/modules/federation/` (schemas, api, service, scheduler,
  peer_client, repository), `app/modules/usage/` (instances view), new Alembic
  revision, tests.
- No change to credential ownership, checkout/checkin, or mirror semantics.
  Usage-report failures never degrade mirroring or routing.
