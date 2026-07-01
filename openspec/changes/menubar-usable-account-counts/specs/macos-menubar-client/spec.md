## ADDED Requirements

### Requirement: Menubar counts and pool capacity reflect only usable accounts

The menubar app MUST derive headline account counts (scope-bar chips and pool-card
account counts) exclusively from authenticated, subscription-usable accounts, and
MUST aggregate pool remaining/capacity credits exclusively from routable accounts.
Unusable accounts MUST remain visible in the accounts list with a state label that
distinguishes "authenticated but unsubscribed" from "disconnected / needs re-auth".

#### Scenario: Canceled subscriptions do not inflate headline counts

- **GIVEN** a provider scope containing accounts where one has subscription ledger status `canceled`
- **WHEN** the scope-bar chips and pool cards render
- **THEN** the canceled account is excluded from every headline count
- **AND** its credits are excluded from the pool remaining-% aggregation

#### Scenario: Disconnected accounts do not inflate headline counts

- **WHEN** an account has status `deactivated` or `reauth_required` (with or without a deactivation reason)
- **THEN** it is excluded from headline counts
- **AND** a `reauth_required` account renders with the re-auth treatment even when its deactivation reason is nil

#### Scenario: Paused accounts count as stored but not as capacity

- **GIVEN** a paused account that is authenticated and subscription-usable
- **WHEN** the pool window aggregates credits
- **THEN** the paused account's credits are excluded from remaining/capacity
- **AND** the account still appears in headline counts and the accounts list as paused

#### Scenario: Unsubscribed accounts stay visible

- **WHEN** an account's subscription ledger status is `canceled`
- **THEN** the accounts list shows it as a dimmed row labeled `unsubscribed`
- **AND** it is not silently removed from the default list
