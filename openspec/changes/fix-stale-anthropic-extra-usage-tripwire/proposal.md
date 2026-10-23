## Why

The Anthropic extra-usage tripwire (`anthropic_extra_usage` quota key) is recorded when a 200 response reports that the subscription window is exhausted and the request was billed to extra usage. The marker carried its own cooldown and kept excluding the account from standard routing even after the latest primary-window snapshot showed headroom again. Healthy accounts stayed skipped, Claude Code startup requests drained the weaker remaining accounts, and some sessions stalled for minutes.

## What Changes

- Standard Anthropic eligibility ignores the extra-usage tripwire once a primary-window snapshot recorded at or after it shows headroom (below 100%, or the window reset has passed).
- Until then (no snapshot, or only an older one) the tripwire still blocks, so the immediate rotation after the first extra-usage response is kept.
- The transient requested-quota cooldown bypass applies the same rule.
- Paid extra-usage fallback, requested-quota cooldowns, and secondary/weekly exhaustion gates are unchanged.

## Impact

- Code: `app/modules/proxy/anthropic_service.py` (`_provider_quota_eligibility`).
- Specs: `account-routing`.
