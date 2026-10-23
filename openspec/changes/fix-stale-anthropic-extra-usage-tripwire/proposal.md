## Why

The response-level extra-usage tripwire (`anthropic_extra_usage` quota key)
records a cooldown when a 200 response reports the subscription window
exhausted. Selection honored that cooldown for its full lifetime even after
the account's primary window recovered, so a healthy account stayed out of
routing. Claude Code startup requests then burned through the weaker
remaining accounts and sometimes stalled for minutes.

## What Changes

- Anthropic selection ignores an active extra-usage tripwire for an account
  whose latest primary usage snapshot shows ordinary headroom: utilization
  below 100 percent, or an exhausted window whose reset has already passed.
- The tripwire still blocks an account whose primary window is exhausted, or
  whose primary state is unknown (no snapshot), so it stays a real safety gate
  against billing extra-usage credits.
- Request-quota cooldowns written by upstream 429 responses are unchanged.

## Impact

- Code: `app/modules/proxy/anthropic_service.py` (`_provider_quota_eligibility`).
- Specs: `account-routing`.
