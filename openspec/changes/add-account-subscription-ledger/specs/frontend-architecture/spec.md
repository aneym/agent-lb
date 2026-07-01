## ADDED Requirements

### Requirement: Accounts page surfaces local subscription renewal metadata

The Accounts page SHALL expose a local per-account subscription ledger containing subscription status, next charge timestamp, active-until timestamp, charge amount/currency, last verified timestamp, and free-form notes. This ledger is operator-maintained and SHALL NOT imply that agent-lb has modified vendor-side billing.

#### Scenario: Account list shows next charge date

- **WHEN** an account has a local `subscriptionNextChargeAt` value
- **THEN** the account list item displays a compact next-charge label for that account

#### Scenario: Account detail edits subscription ledger

- **WHEN** a user saves subscription ledger values from the account detail view
- **THEN** the dashboard calls the account subscription update API
- **AND** refreshes account data after a successful save

#### Scenario: Active-until date remains distinct from next charge

- **WHEN** an account subscription has an active-until timestamp
- **THEN** the account detail view distinguishes the active-until timestamp from the next charge timestamp

#### Scenario: Load-balancer pause stays separate from vendor subscription state

- **WHEN** a user marks an account subscription as `pause_pending`, `paused`, or `canceled`
- **THEN** the account's load-balancer account status is not changed unless the user separately invokes the existing pause/resume action

### Requirement: Local browser profile registry supports vendor account operations

Project operator guidance SHALL define a gitignored local account profile registry for mapping agent-lb accounts to dedicated browser profiles and SHALL prohibit storing passwords, tokens, card numbers, or recovery codes in that registry.

#### Scenario: Agent opens the matching vendor account profile

- **WHEN** a future agent is asked to inspect or update a vendor-side subscription for an account with a local registry entry
- **THEN** the agent uses the account's dedicated browser profile rather than a shared browser cookie jar
- **AND** records completed vendor-side subscription actions in the local registry and agent-lb ledger
