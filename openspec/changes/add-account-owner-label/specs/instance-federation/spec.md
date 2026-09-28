# instance-federation: delta

## ADDED Requirements

### Requirement: Account summaries identify remote owners for display

`GET /api/accounts` SHALL expose nullable `ownerLabel` for each account. For locally owned accounts it MUST be null. For a remotely owned account whose `ownerInstance` matches a push source binding in `federation-push-state.json`, it MUST be that source's name. Otherwise it MUST fall back to `ownerInstance`. Missing or unreadable binding state MUST NOT cause the accounts request to fail. This field is informational and MUST NOT alter account routing or refresh ownership. The macOS menubar SHALL distinguish shared accounts beside the plan with a source label, and SHALL replace the source identity with `SHARED` in privacy mode, including accessible descriptions and tooltips.

#### Scenario: Pushed account has a named source

- **GIVEN** a remote account with an owner instance bound to push source `nate`
- **WHEN** the dashboard requests `GET /api/accounts`
- **THEN** its `ownerLabel` is `nate` and the menubar shows `VIA NATE`

#### Scenario: Pull mirror without a push binding

- **GIVEN** a mirrored account with no push source binding
- **WHEN** the dashboard requests `GET /api/accounts`
- **THEN** its `ownerLabel` is its `ownerInstance`

#### Scenario: Local account

- **GIVEN** a locally owned account
- **WHEN** the dashboard requests `GET /api/accounts`
- **THEN** its `ownerLabel` is null and no shared chip is shown

#### Scenario: Missing or unreadable push state

- **GIVEN** a remote account and a missing or unreadable push state file
- **WHEN** the dashboard requests `GET /api/accounts`
- **THEN** the request succeeds and its `ownerLabel` is its `ownerInstance`

#### Scenario: Privacy masking of a shared account

- **GIVEN** a shared account and privacy masking enabled
- **WHEN** the menubar shows the account
- **THEN** its shared chip and accessible description show only `SHARED`, not its source name
