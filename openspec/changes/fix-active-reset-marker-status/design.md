## Context
The Nov 13 reset recovered Dan from weekly exhaustion. The account is active with headroom, but a Nov 17 rateLimitResetAt survives. _state_from_account seeds reset_at with db_reset_at or runtime.reset_at; _persist_state_if_current writes that value back with CAS. select_account checks reset_at only for RATE_LIMITED/QUOTA_EXCEEDED. The CLI instead blocks any active account with a future marker.

## Decisions
Drop the reset seed only for persisted ACTIVE accounts. Existing quota application may still produce a blocked status/reset. Leave runtime cooldown_until, error-rule cooldowns, blocked_at, Kimi monthly logic, quota bypasses, locks, versions and CAS untouched. Remove only the standalone active-marker status block; retain raw marker output for diagnosis and real exhausted-window checks.

## Non-goals
No account updates, reset redemption, changes to warmups, routing eligibility, paid fallback, or serving runtime. No masking exhausted quotas with ACTIVE.

## Validation and rollout
Use ./check, strict OpenSpec validation and worktree CLI against the existing read-only ax42 HTTP forward. Parent deploys the exact landed files and confirms marker clearing on a subsequent selection and truthful status. Keep this change unarchived until deployment proof.
