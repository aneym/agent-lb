## Context

The extra-usage tripwire is written when Anthropic returns a successful response that is billing overage credits. The row intentionally lives until the vendor reset so subsequent requests do not silently spend more. A later usage refresh can show that the subscription primary window has recovered before that historical reset. Eligibility currently reads the tripwire independently and continues blocking the account, even though standard subscription traffic is safe again.

## Goals / Non-Goals

**Goals:**

- Admit an account with current primary subscription headroom even when an older extra-usage tripwire remains active.
- Keep the tripwire authoritative whenever the current primary window is exhausted.
- Preserve requested-quota cooldowns and all other safety gates.

**Non-Goals:**

- Clearing historical usage rows or changing account credentials, billing, subscription, or pause state.
- Weakening upstream 429 cooldowns.
- Changing Claude Code's startup model selection.

## Decisions

Eligibility will ignore the dedicated extra-usage tripwire only when a primary-window snapshot with headroom was recorded after the tripwire. This derives routing from current subscription evidence while retaining immediate protection when a tripwire is newer than the last usage refresh.

The implementation will not mutate or delete the tripwire during refresh. Keeping the row preserves evidence and avoids a race where an older usage refresh erases a newer paid-traffic warning.

Regression coverage will exercise the external `/v1/messages` route with two accounts: one healthy account carrying an active historical tripwire and one unavailable alternative. Success must be attributable to the healthy account. Existing paid-fallback and tripwire rotation tests continue proving the exhausted-account safety behavior.

## Risks / Trade-offs

- **Risk:** A falsely optimistic primary usage snapshot could admit traffic that bills credits. **Mitigation:** Existing usage freshness, requested-quota cooldown, and response tripwire logic remain unchanged; any billed response rewrites the tripwire and the next current usage refresh determines subscription state.
- **Risk:** Clearing the live symptom without fixing parallel Fable reservation pressure. **Mitigation:** Validate both model-specific account probes and a fresh live Claude Code request after deployment.
