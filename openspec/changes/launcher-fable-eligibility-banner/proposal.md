## Why

The `cc` launcher banner shows the selected Claude account, model, scoped
quota, 5-hour quota, and weekly quota, but it does not say whether the selected
account is still eligible for Fable-class routing. Operators currently have to
infer that from weekly usage or open the menubar/dashboard.

## What Changes

- Include the selected account's `/api/accounts` `fableEligible` state in the
  `cc` startup banner when that field is available.
- Show `fable available` for `fableEligible: true` and `fable out` for
  `fableEligible: false`.
- Preserve the existing fallback banner when `/api/accounts` enrichment is not
  available.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `account-routing`: the Claude launcher surfaces selected-account Fable
  availability from the existing account summary contract.

## Impact

- Affected code: `clients/claude-lb-launch`, `tests/unit/test_claude_lb_launch.py`.
- Affected API: consumes the existing `/api/accounts` `fableEligible` field; no
  server schema or endpoint change.
- Validation: launcher unit tests, `py_compile`, dry-run launcher banner,
  OpenSpec validation.
