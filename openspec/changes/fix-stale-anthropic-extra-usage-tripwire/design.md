## Context

`_provider_quota_eligibility` loads three Anthropic gates per account: requested-quota cooldowns, primary/secondary usage snapshots, and the dedicated extra-usage tripwire. The tripwire is written from response headers the moment a 200 is billed to extra usage, with the subscription window's reset as its cooldown end. That is before the usage refresher (about 60 s later) writes an exhausted primary snapshot, which is the whole point of the tripwire. When a later snapshot showed the window had headroom again, the tripwire still won and the account stayed out of routing until the old reset.

## Decision

The tripwire is a signal about the primary window, so the newest primary evidence decides. A single helper, `_extra_usage_tripwire_is_active`, returns true when the tripwire cooldown is active and there is no primary snapshot showing headroom that was recorded at or after the tripwire. Headroom means the latest primary snapshot is not an active exhaustion (below 100%, or its reset has passed or is unknown). Both the standard eligibility loop and the transient cooldown bypass use the helper.

- No primary snapshot, or only one older than the tripwire: the tripwire blocks. This keeps the immediate rotation after the first extra-usage response.
- Newer snapshot that is still exhausted: the account is blocked by primary exhaustion anyway; the tripwire stays a real safety gate.
- Newer snapshot with headroom: the tripwire is stale and ignored.

Timestamps are compared as epoch seconds. Tripwire rows use the database `recorded_at` default (second precision), so a snapshot in the same second counts as newer.

## Alternatives considered

- Honor the tripwire only while the primary snapshot is exhausted. Rejected: it reopens the gap the tripwire closes, because the snapshot taken just before the extra-usage response still shows headroom (breaks `test_credit_billing_response_trips_cooldown_and_rotates_next_request`).
- Clear the tripwire row when the usage refresher sees headroom. Rejected: adds a write path in another module for state that can be derived at read time.

## Example

Account A: tripwire recorded 10:00, cooldown until 15:00; primary snapshot at 10:05 shows 12%. At 13:30 A is eligible. Account B: same tripwire, latest primary snapshot at 09:59 shows 98%. B stays blocked; the next refresh decides.
