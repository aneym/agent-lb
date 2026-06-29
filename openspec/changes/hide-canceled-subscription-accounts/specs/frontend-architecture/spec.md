## ADDED Requirements

### Requirement: Accounts page can recheck canceled subscriptions

The Accounts page SHALL keep locally `canceled` subscription accounts visible
and SHALL expose a "Check sub" action for those accounts. Invoking the action
SHALL call a backend subscription-check endpoint for the selected account and
refresh account data after completion.

#### Scenario: Canceled account exposes Check sub action

- **WHEN** the selected account's local subscription status is `canceled`
- **THEN** the account detail view shows a "Check sub" action

#### Scenario: Successful check restores subscription visibility

- **WHEN** a canceled account has been reactivated upstream
- **AND** the user invokes "Check sub"
- **THEN** the backend verifies the account can complete a minimal upstream
  request
- **AND** the local subscription status becomes `active`
- **AND** the account data refresh makes the account eligible for non-Accounts
  surfaces again

#### Scenario: Failed check keeps account canceled

- **WHEN** a canceled account still cannot complete the backend subscription
  check
- **AND** the user invokes "Check sub"
- **THEN** the local subscription status remains `canceled`
- **AND** the account remains visible in the Accounts page

#### Scenario: Check endpoint is limited to canceled accounts

- **WHEN** a non-canceled account is submitted directly to the backend
  subscription-check endpoint
- **THEN** the backend rejects the check
- **AND** the local subscription ledger is not changed

### Requirement: Operational surfaces exclude canceled subscriptions

Dashboard and client surfaces outside the Accounts page SHALL exclude locally
`canceled` subscription accounts from headline pool counts and selectable pool
summaries unless a surface explicitly labels a value as all stored accounts.

#### Scenario: Headline pool count ignores canceled subscriptions

- **GIVEN** one active account has local subscription status `canceled`
- **AND** one active account has local subscription status `active`
- **WHEN** an operational summary computes subscription-usable pool size
- **THEN** only the non-canceled account contributes to that count

#### Scenario: Accounts view still lists canceled subscriptions

- **GIVEN** an account has local subscription status `canceled`
- **WHEN** the user opens the Accounts page
- **THEN** that account remains present in the account list
