## 1. Implementation

- [x] 1.1 Remove `cancel_pending` from backend and frontend subscription status schemas.
- [x] 1.2 Normalize legacy stored `cancel_pending` values to `active` in account API responses.
- [x] 1.3 Remove the status from the Accounts subscription dropdown and operator guidance.
- [x] 1.4 Update tests and existing OpenSpec text that taught operators to use `cancel_pending`.

## 2. Verification

- [x] 2.1 Run backend and frontend tests covering subscription ledger status behavior.
- [x] 2.2 Run OpenSpec validation.
- [x] 2.3 Run an adversarial review from a separate agent before committing.
