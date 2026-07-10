# Menubar Fable Focus Ring

## Why

The menubar status icon shows the primary and long-window pool rings, but the
operator's most common glance question while actually driving Claude is "how
much Fable do I have left before reset?" — today that requires opening the
panel and scanning per-account FABLE labels. The accounts API already ships
the per-account Fable-scoped weekly window (`anthropic_fable_scoped_weekly`).

## What Changes

- Track the frontmost application via NSWorkspace activation notifications.
- While a Claude app (bundle id prefix `com.anthropic.`) is frontmost, the
  status icon widens with a third circle: a ring showing pool-level Fable
  remaining % with a small `F` glyph centered. It disappears when focus moves
  to a non-Claude app.
- Pool-level Fable remaining % = mean scoped-weekly remaining % across
  routable Anthropic accounts that report the scoped window; exhausted but
  routable accounts stay in the denominator. No data → track-only circle.
- Extend menubar unit tests for the pool math, bundle-id matching, and icon
  geometry.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `macos-menubar-client`: status icon gains a Claude-focus Fable ring driven
  by existing `/api/accounts` data.

## Impact

- Affected code: `clients/macos-menubar/**` only; no server changes.
- Affected API: consumes existing `/api/accounts` `additionalQuotas` data.
- Validation: Swift menubar tests/build, live focus-toggle verification
  against the running service, OpenSpec validation.
