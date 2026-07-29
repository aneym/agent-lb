# Tasks

- [x] Add `anthropic_fable_scoped_weekly` to
      `config/additional_quota_registry.json` with display label
      "Fable weekly (scoped)"; verify label resolution.
- [x] Add `fableEligible` to the frontend account schema and a shared
      `getFableQuota` helper with unit tests.
- [x] Accounts page: Fable meter in list rows (3-column grid) and promoted
      Fable row in the usage panel with additional-quotas dedupe; tests.
- [x] Dashboard: Fable bar on account cards (provider-aware Session label)
      and `fablePoolRunway` + "Fable runway (weekly scoped)" stat tile;
      tests.
- [x] Validate (vitest, typecheck, lint), build static assets, restart the
      live service, verify live payload label and dashboard, push to main.
