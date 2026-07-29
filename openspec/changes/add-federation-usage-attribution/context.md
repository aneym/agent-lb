# Context — add-federation-usage-attribution

## Decisions

- **Instance id = user identity.** The federation `local_instance_id`
  (e.g. `studio`, `macbook-pro-110`, a partner's machine) is the attribution
  unit. One machine ≈ one person in this pool; no user/tenant entity is
  introduced. If finer-than-machine identity is ever needed, API keys already
  attribute per-request (`request_logs.api_key_id`) and can layer on top.
- **Push, not pull.** Followers know the owner's URL
  (`federation_peer_url`); the owner does not know followers' addresses, and
  followers may be asleep or NATed. Piggybacking on the mirror cycle reuses
  the existing scheduler, peer client, backoff, and token auth.
- **Absolute daily aggregates, not deltas.** Replace-on-upsert by
  (`instance_id`, `account_id`, UTC `day`) makes redelivery idempotent with
  no watermark/ack protocol. The trailing window (7 days) bounds payload
  size; older history simply stops being refreshed (it remains stored).
- **Symmetric endpoint.** `GET /api/usage/instances` works on any instance:
  live local rollup + whatever reports it happens to hold. Only the owner
  holds everyone's reports, so only the owner sees the full pool — no
  owner-only code path.

## Failure modes

- Owner offline: pushes fail with the mirror pull and back off together;
  reports refresh on reconnection (window >> outage in practice).
- Clock skew between instances shifts day-bucket edges by at most the skew;
  acceptable for attribution (not billing).
- A malicious/buggy peer with the federation token could only write usage
  rows for its claimed instance id — credential and routing state are
  untouched.

## Example

Partner's Mac (`instance_id=partner-mba`) routes 300 requests against the
studio-owned `alex@kineticapps.io` Anthropic account on 2026-07-30. Within
one mirror interval (60 s) studio's `GET /api/usage/instances` shows under
`partner-mba` → `2026-07-30` → that account: 300 requests, token totals, and
cost, alongside studio's and macbook's own entries.
