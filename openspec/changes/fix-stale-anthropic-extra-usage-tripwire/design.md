## Context
The router reads the latest subscription usage and a separate persisted extra-usage marker. The marker can remain active after subscription headroom returns.

## Goals / Non-Goals
Restore subscription routing after recovery while preserving protection against unintended paid usage. Retain existing request-quota, weekly, model, and paid-fallback rules.

## Decisions
Keep an extra-usage marker when primary exhaustion is active, when no primary snapshot exists, or when the latest primary snapshot was recorded at or before the marker. Clear it only when a strictly newer primary snapshot no longer shows active exhaustion. Active exhaustion requires usage of at least 100 percent and a known future reset. Compare recorded_at timestamps inside the repository session and filter the marker snapshot once so ordinary eligibility and transient-cooldown recovery consume the same effective markers. Do not erase persisted history or write database state during selection.

For example, an account with a newer snapshot at 20 percent primary usage than its unexpired extra-usage marker is eligible for normal routing; an account at 100 percent with a future reset remains blocked under the existing subscription policy. Missing or expired primary reset horizons establish recovery only in a snapshot newer than the marker. A fresh credit-billing response remains authoritative over older headroom, and equal timestamps retain the safety gate.

## Risks / Trade-offs
A recovered account can still receive a genuine upstream 429; the existing request-quota cooldown records that response. Independent cooldowns and secondary exhaustion remain authoritative. Explicit opt-in paid fallback retains its existing semantics.

## Validation
Run the supplied acceptance script and relevant Anthropic proxy coverage. Validate the OpenSpec artifacts strictly. No test files are admitted by this brief; the acceptance runner supplies regression coverage externally.
