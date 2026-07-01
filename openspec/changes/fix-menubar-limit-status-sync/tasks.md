## 1. Specification

- [x] 1.1 Add menu bar requirements for status classification and subscription ledger display.

## 2. Implementation

- [x] 2.1 Update the menu bar account model to decode subscription ledger data.
- [x] 2.2 Update account row/filter classification so future reset metadata does not create synthetic limited status for active accounts.
- [x] 2.3 Show compact subscription ledger labels for otherwise active rows.
- [x] 2.4 Record operator-reported subscription ledger entries without changing routing state.

## 3. Verification

- [x] 3.1 Add/update unit coverage for active accounts with future reset metadata, blocked accounts with reset metadata, and paused precedence.
- [x] 3.2 Run menu bar tests/build.
- [x] 3.3 Validate OpenSpec change.
  - `npx --yes @fission-ai/openspec@latest validate fix-menubar-limit-status-sync --strict`
    passed.
- [x] 3.4 Verify live `/api/accounts` reflects subscription ledger without changing routing status.
