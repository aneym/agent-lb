## Context
The router reads the latest subscription usage and a separate persisted extra-usage marker. The marker can remain active after subscription headroom returns.

## Goals / Non-Goals
Restore subscription routing after recovery while preserving protection against unintended paid usage. Retain existing request-quota, weekly, model, and paid-fallback rules.

## Decisions
Use the existing primary-exhaustion map as the authority for whether an extra-usage marker is relevant. It requires usage of at least 100 percent and a known future reset. Filter the marker snapshot once so ordinary eligibility and transient-cooldown recovery consume the same effective markers. Do not erase persisted history or write database state during selection.

For example, an account at 20 percent primary usage with an unexpired extra-usage marker is eligible for normal routing; an account at 100 percent with a future reset remains blocked under the existing subscription policy. Missing or expired primary reset horizons retain existing recovery semantics.

## Risks / Trade-offs
A recovered account can still receive a genuine upstream 429; the existing request-quota cooldown records that response. Independent cooldowns and secondary exhaustion remain authoritative. Explicit opt-in paid fallback retains its existing semantics.

## Validation
Run the supplied acceptance script and relevant Anthropic proxy coverage. Validate the OpenSpec artifacts strictly. No test files are admitted by this brief; the acceptance runner supplies regression coverage externally.
