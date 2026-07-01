## ADDED Requirements

### Requirement: Subscription ledger excludes cancellation-pending status

The Accounts page and account subscription update API SHALL NOT offer or accept
`cancel_pending` as a subscription ledger status. The supported local
subscription statuses SHALL be `active`, `pause_pending`, `paused`, and
`canceled`, with `null` representing an untracked ledger status.

#### Scenario: Account detail status choices omit cancellation-pending

- **WHEN** an operator opens the account subscription status selector
- **THEN** the selector does not include `cancel_pending`

#### Scenario: Account subscription update rejects cancellation-pending

- **WHEN** a client submits `cancel_pending` as the account subscription status
- **THEN** the account subscription update API rejects the request

### Requirement: Active-until date is independent ledger metadata

The Accounts page SHALL render a present `currentPeriodEndAt` value as an
active-until timestamp without requiring or displaying a cancellation-pending
status.

#### Scenario: Active account has active-until date

- **WHEN** an account subscription ledger has status `active`
- **AND** the ledger has `currentPeriodEndAt`
- **THEN** the account detail view labels that timestamp as active-until
- **AND** does not display a cancellation-pending status label
