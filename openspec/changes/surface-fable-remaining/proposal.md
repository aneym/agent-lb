## Why

The menubar and `cc` launcher now show whether an account is Fable-eligible,
but operators still have to infer the actual Fable headroom from raw account
details. `/api/accounts` already carries the scoped Fable quota row in
`additionalQuotas`, so the clients can show the remaining percent directly.

## What Changes

- Decode/use the `anthropic_fable_scoped_weekly` quota row from
  `/api/accounts.additionalQuotas`.
- Show the scoped Fable remaining percent in the menubar account chip when the
  quota row is present.
- Show the scoped Fable remaining percent in the `cc` startup banner when the
  selected account includes the quota row.
- Preserve the existing availability-only labels when scoped quota data is not
  available.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `macos-menubar-client`: Claude account rows include scoped Fable remaining
  percent in the compact Fable chip.
- `account-routing`: the Claude launcher includes scoped Fable remaining
  percent in the selected-account startup banner.

## Impact

- Affected code: `clients/macos-menubar/**`, `clients/claude-lb-launch`,
  `tests/unit/test_claude_lb_launch.py`.
- Affected API: consumes the existing `/api/accounts.additionalQuotas` scoped
  Fable quota row; no server schema or endpoint change.
- Validation: Swift menubar tests/build, launcher unit tests, `py_compile`,
  dry-run launcher banner, OpenSpec validation.
