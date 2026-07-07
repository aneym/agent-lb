# Fix Stale Exhausted Quota Rows Permanently Blocking Accounts

## Why

A persisted usage/additional-quota row with `used_percent >= 100` and
`reset_at = None` permanently excludes an Anthropic account from routing, and the
exclusion is self-perpetuating. When upstream's additional-rate-limit payload
omits both `reset_at` and `reset_after_seconds`, the usage updater persists the
exhausted window with `reset_at = None`. Every routing gate then reads a `None`
reset on a 100%-consumed window as "cooling down forever":

- The Anthropic additional-quota cooldown gate treats `reset_at = None` as an
  active cooldown with no expiry.
- The primary-window exhaustion prefilter blocks the account whenever
  `reset_at is None`.
- The dashboard additional-quota window renders a permanent 100% with no reset.

Because the account never serves traffic, it never receives a `429`, so the
`429`/tripwire path that would synthesize a fresh bounded cooldown never runs.
The exclusion is permanent with no self-healing path. This contrasts with the
tripwire path, which always synthesizes a bounded reset (`now + 60s` default)
that self-expires.

## What Changes

- **Write-time invariant**: when the usage updater records an exhausted window
  (`used_percent >= 100`) and upstream omitted both `reset_at` and
  `reset_after_seconds`, synthesize a bounded `reset_at` — `now +
  limit_window_seconds` when the window length is known, otherwise `now + 3600` —
  instead of persisting `reset_at = None`. This covers Anthropic additional-quota
  windows and the primary usage window.
- **Read-time re-admission (covers legacy rows already persisted)**: at the
  Anthropic additional-quota cooldown gate, the primary-window exhaustion
  prefilter, and the dashboard additional-quota window mapping, an exhausted row
  with `reset_at = None` MUST NOT block routing or display as exhausted
  indefinitely. It is treated as re-admitted; only a bounded, still-future
  `reset_at` continues to gate the account.
- Erring toward re-admission is safe: a genuinely exhausted re-admitted account
  triggers an upstream `429`, and the tripwire writes a fresh bounded cooldown.

Monthly-window semantics are preserved. The three read-time gates only consult
Anthropic additional-quota windows (always `primary`/`secondary` model quotas,
never monthly) and the `usage` `primary` (5h) window. Free-account monthly quota
lives in the `usage` `monthly` window and is never consulted by these gates, so
re-admitting a `None`-reset exhausted row does not affect monthly-cap behavior.
The write-time synthesis is likewise scoped to additional-quota windows and the
primary window; `secondary` and `monthly` writes are unchanged.

## Impact

- Affected specs: `usage-refresh-policy` (write-time bounded reset),
  `account-routing` (read-time re-admission at routing gates and quota
  presentation).
- Affected code: `app/modules/usage/updater.py`,
  `app/modules/proxy/anthropic_service.py`, `app/modules/accounts/service.py`.
- Behavior change: accounts stuck behind a stale `used_percent = 100`,
  `reset_at = None` row become selectable again; newly written exhausted windows
  carry a bounded, self-expiring reset. No schema or migration change — this is a
  code-semantics fix that heals legacy rows at read time.
