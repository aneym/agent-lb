## Why

An Anthropic account can recover its subscription headroom and answer a live probe successfully while an older extra-usage tripwire remains active. That stale marker currently excludes the healthy account from ordinary subscription routing, causing Claude Code startup requests to exhaust weaker accounts and remain held open for minutes.

## What Changes

- Treat the extra-usage tripwire as relevant only while the account's current primary subscription window is exhausted.
- Preserve the tripwire as a paid-usage safety gate when primary headroom is actually exhausted.
- Add regression coverage for recovered subscription headroom with a still-active historical tripwire.
- Verify every configured Anthropic account with purpose-scoped model probes and exercise the live Claude Code routing path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Reconcile persisted extra-usage tripwires with the account's current primary subscription window before excluding it from standard routing.

## Impact

The change affects Anthropic quota eligibility in `app/modules/proxy/anthropic_service.py`, its proxy regression tests, and the live local routing behavior. It does not change account credentials, subscriptions, billing settings, or public request schemas.
