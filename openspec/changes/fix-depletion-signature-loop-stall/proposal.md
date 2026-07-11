# Fix depletion history signature freezing the event loop

## Why

On 2026-07-11 the studio instance froze repeatedly (25–50s at a time, every
1–2 minutes under load): `/health` timed out while the process stayed alive,
the watchdog kickstarted the service, and every in-flight chat died. A native
stack sample taken mid-stall showed the event loop spending ~98% of its time
in one task calling `repr()` on datetime/float/int values.

Root cause: `filter_depletion_history_since()` computed a blake2b content
digest with `repr()` per row over every in-window usage-history row — ~105k
`usage_history` plus ~145k `additional_usage_history` rows in the 7-day
window — on every dashboard/menubar depletion poll, per account, on the
proxy's event loop. The digest existed only as a cache-invalidation key, so
the "cache" cost O(rows) on hit and miss alike.

## What Changes

- The depletion EWMA cache signature is now edges-only: `(row_count,
  first-row edge tuple, latest-row edge tuple)` — O(1) to compare and O(n)
  only in list building, with no per-row `repr()`/hashing work.
- The per-row content digest (`_update_history_digest`,
  `_history_signature_from_rows`) is removed.
- Contract change vs #588: an in-place mutation confined to interior rows
  with identical edges is no longer detected. Accepted deliberately: no write
  path mutates interior usage-history rows (the only UPDATE on these tables
  reassigns `account_id` during account merges, which changes row membership
  and therefore the edge signature), and the digest that guarded this
  hypothetical froze production.

## Impact

- Affected specs: usage-refresh-policy (depletion metrics caching)
- Affected code: `app/modules/usage/depletion_service.py`,
  `tests/unit/test_depletion_service.py`
