# macOS Menu Bar Client

## ADDED Requirements

### Requirement: Menu bar status classification follows backend routing state

The menu bar account list SHALL classify an account as rate-limited only when
the backend account status is a blocked routing state such as `rate_limited` or
`quota_exceeded`. Future reset metadata SHALL be used only to display reset
details for already-blocked accounts and SHALL NOT by itself move an `active`
account into the rate-limited filter or row presentation.

#### Scenario: Active account has future reset metadata

- **WHEN** an account has status `active`
- **AND** `rateLimitResetAt` is in the future
- **THEN** the row remains active
- **AND** the Rate-limited filter excludes the account
- **AND** the Active filter includes the account

#### Scenario: Blocked account has reset metadata

- **WHEN** an account has status `rate_limited` or `quota_exceeded`
- **AND** `rateLimitResetAt` is present
- **THEN** the row shows a rate-limited status with the reset detail
- **AND** the Rate-limited filter includes the account

#### Scenario: Operator-disabled states take precedence

- **WHEN** an account is paused or deactivated
- **AND** `rateLimitResetAt` is in the future
- **THEN** the row and status filter classify the account as paused or inactive
  according to the backend routing state

### Requirement: Menu bar surfaces local subscription ledger without changing routing state

The menu bar account list SHALL decode the account subscription ledger returned
by `/api/accounts` and render compact subscription labels for otherwise active
rows whose local subscription status is `cancel_pending`, `pause_pending`,
`paused`, or `canceled`. Rendering a subscription label SHALL NOT change the
load-balancer status, pause the account, deactivate it, or move it out of the
Active status filter.

#### Scenario: Cancel-pending subscription remains routable

- **WHEN** an account has backend status `active`
- **AND** its local subscription status is `cancel_pending`
- **THEN** the account row displays a compact cancel-pending or active-until
  label
- **AND** the account remains classified as active

#### Scenario: Subscription pause is distinct from routing pause

- **WHEN** an account has backend status `active`
- **AND** its local subscription status is `paused`
- **THEN** the row may display a subscription-paused label
- **AND** the account is not classified as routing-paused unless the backend
  account status is `paused`
