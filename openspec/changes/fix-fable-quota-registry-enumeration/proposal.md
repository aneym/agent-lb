# Fix: surface the Fable scoped-weekly quota after registry-driven enumeration

## Why

Commit `1cfbc556` ("fix(accounts): avoid quota history discovery scan") switched
`/api/accounts` additional-quota assembly from discovering quota keys present in
`additional_usage_history` to enumerating the static registry
(`config/additional_quota_registry.json`). The Anthropic Fable scoped-weekly
marker (`anthropic_fable_scoped_weekly`) is written by the usage updater outside
that registry, so its row silently disappeared from `additionalQuotas`. The
macOS menubar and the `cc` launcher banner read the Fable remaining percent from
that row (see the `surface-fable-remaining` change), so both regressed to the
availability-only "FABLE" / "FABLE OUT" label with no percent.

## What Changes

- `AccountsService._additional_quotas_by_account` explicitly appends
  `anthropic_fable_scoped_weekly` to the registry-derived quota-key list, so the
  row is enumerated again without reintroducing the discovery scan.
- Regression test pinning the behavior:
  `tests/unit/test_accounts_service_additional_quotas.py`.

## Impact

- Affected specs: `account-routing` (accounts API additional-quota exposure).
- Affected code: `app/modules/accounts/service.py`.
- Clients (menubar, `cc` banner) recover the Fable remaining percent with no
  client-side changes.
