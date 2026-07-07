## Why

The macOS menubar already shows Claude account quota rows, but it does not
surface whether each account can still serve Fable-class requests. Operators
have to infer Fable availability from weekly usage, even though `/api/accounts`
already exposes the authoritative `fableEligible` routing signal.

## What Changes

- Decode `fableEligible` in the macOS menubar account model.
- Render a compact per-row Fable availability indicator for Claude accounts:
  `Fable` when available, `Fable out` when exhausted or otherwise not routable
  for Fable.
- Preserve the existing fixed account-row height and leave non-Claude rows
  unchanged.
- Extend menubar fixture/model tests so consumed API shape regressions fail
  locally.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `macos-menubar-client`: account rows surface per-account Fable availability
  from the existing account summary contract.

## Impact

- Affected code: `clients/macos-menubar/**`.
- Affected API: consumes the existing `/api/accounts` `fableEligible` field;
  no server schema or endpoint changes.
- Validation: Swift menubar tests/build plus OpenSpec validation.
