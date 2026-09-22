## Why

The macOS menu-bar can show a future reset countdown and `+0 cr` while a quota
window is already at its known capacity. The reset timestamp is a window
boundary, not evidence that allowance will be restored.

## What Changes

- Render a known-full pool window as `Full · no reset needed`.
- Omit countdowns for known-full per-account windows and omit known-full
  accounts from scoped reset recovery/schedules.
- Preserve unknown telemetry and show fractional positive recovery as `+<1 cr`.

## Impact

- Affected client: `clients/macos-menubar` only.
- No API, quota, routing, reservation, or persisted-data behavior changes.
