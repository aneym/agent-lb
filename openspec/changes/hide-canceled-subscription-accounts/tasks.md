# Tasks

- [x] Add API/service support for checking a canceled subscription account.
- [x] Exclude `subscription_status=canceled` accounts from routing candidate
  selection and non-Accounts summary surfaces.
- [x] Add Accounts UI affordance for "Check sub" on canceled subscriptions.
- [x] Add focused backend, frontend, and menubar tests.
- [x] Verify live account ledger state after marking failed canceled accounts.
- [x] Validate OpenSpec change.
  - `npx --yes @fission-ai/openspec@latest validate hide-canceled-subscription-accounts --strict`
    passed.
