## Context

`_provider_quota_eligibility` loads the latest `anthropic_extra_usage`
cooldown per account and, before this change, excluded any account whose
cooldown was still active. The tripwire is written from response headers and
its reset can outlive the window that triggered it, so a later usage refresh
can show the window has recovered while the cooldown row is still active.

## Decisions

- Headroom is read from the same latest primary snapshot the selector already
  loads for primary exhaustion; no new query or schema.
- "Headroom" means `used_percent < 100`, or `used_percent >= 100` with a
  `reset_at` at or before now (the window rolled over).
- No primary snapshot, or a snapshot without `used_percent`, keeps the
  tripwire in force. Failing closed bounds credit burn; the next usage refresh
  fills the missing snapshot.
- Snapshots are not compared with the tripwire's `recorded_at`. Both are
  written within the ~60s refresh cadence, so ordering is noisy; the latest
  primary snapshot is the single source of truth for window state.

## Risks

- A primary snapshot taken just before the window exhausted can re-admit an
  account for up to one refresh interval. The next response that bills credits
  rewrites the tripwire, and the next refresh marks the primary window
  exhausted, which the primary gate already blocks.

## Example

Account A has an active tripwire with a reset two hours away; its latest
primary snapshot reads 12 percent. A is eligible. Account B has the same
tripwire and a primary snapshot at 100 percent with a reset one hour away; B
stays blocked.
