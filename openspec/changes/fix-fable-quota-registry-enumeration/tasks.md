# Tasks

## 1. Fix

- [x] 1.1 Append `anthropic_fable_scoped_weekly` to the registry-derived quota-key
  enumeration in `AccountsService._additional_quotas_by_account`
- [x] 1.2 Add regression test proving the fable row surfaces in
  `additionalQuotas` (red without the fix, green with it)

## 2. Validation

- [x] 2.1 Related unit suites pass (`test_account_mappers`,
  `test_accounts_service_*`, `test_hot_path_caches`, `test_additional_*`)
- [x] 2.2 Ruff clean; strict OpenSpec validation passes
- [x] 2.3 Live `/api/accounts` on the deployed service returns the
  `anthropic_fable_scoped_weekly` row and the menubar/`cc` banner show the
  Fable percent again
