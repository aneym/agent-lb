## ADDED Requirements

### Requirement: Successful account-pinned Fable probes reconcile stale model cooldowns

The account pulse SHALL probe a routable Anthropic account when it has an active persisted `anthropic_top` or `anthropic_top_thinking` cooldown. Each probe MUST match its quota shape: plain Fable for `anthropic_top` and adaptive-thinking Fable for `anthropic_top_thinking`. A successful probe MUST append cleared state only for the probed cooldown key and only if the cooldown row observed before the probe is still the latest row for that account, quota key, and window. A non-successful or inconclusive probe MUST NOT clear that cooldown. Reconciliation MUST NOT modify general usage, unrelated quota families, account status, or subscription state.

#### Scenario: Successful high-effort probe clears stale cooldowns

- **GIVEN** a routable Anthropic account has an active persisted Fable model-quota cooldown
- **WHEN** its account-pinned high-effort Fable probe succeeds
- **THEN** the pulse appends a zero-percent cooldown row only for that quota key
- **AND** the account becomes eligible for selection under that quota key

#### Scenario: Failed probe preserves cooldowns

- **GIVEN** a routable Anthropic account has an active persisted Fable model-quota cooldown
- **WHEN** its account-pinned high-effort Fable probe returns a 429, refusal, server error, or transport failure
- **THEN** the pulse leaves that cooldown unchanged

#### Scenario: Reconciliation is quota-scoped

- **GIVEN** an account has both a Fable model-quota cooldown and unrelated usage or quota records
- **WHEN** a successful probe reconciles the Fable model-quota cooldown
- **THEN** only the active `anthropic_top` and `anthropic_top_thinking` cooldown keys are cleared
- **AND** general usage, unrelated quota records, account status, and subscription state remain unchanged

#### Scenario: Concurrent newer cooldown wins

- **GIVEN** the pulse observes an active Fable model-quota cooldown and starts its account-pinned probe
- **AND** a newer cooldown row is recorded for the same account and quota key before the probe completes
- **WHEN** the older probe succeeds
- **THEN** its compare-and-append clear is a no-op
- **AND** the newer cooldown remains the latest effective state
