## ADDED Requirements

### Requirement: Team member keys always carry an account scope

The system SHALL require at least one existing assigned account when minting a team member key and SHALL reject updates that clear all assigned accounts from a member key. Non-member keys MAY be unscoped. Updates to an existing member key that do not modify assigned accounts SHALL remain allowed.

#### Scenario: Mint without accounts is refused

- **WHEN** a team member key is requested without assigned accounts or with an empty assigned account list
- **THEN** the system SHALL return an account-scope-required error
- **AND** the system SHALL NOT create the key

#### Scenario: Mint with accounts succeeds and is scoped

- **WHEN** a team member key is requested with at least one existing assigned account
- **THEN** the system SHALL create the key with account assignment scoping enabled
- **AND** the key SHALL be assigned only the requested accounts

#### Scenario: Clearing a member key's accounts is refused

- **GIVEN** a key belongs to a team member and has assigned accounts
- **WHEN** its assigned accounts are updated to an empty list
- **THEN** the system SHALL return an account-scope-required error
- **AND** its assignments and account assignment scope SHALL remain unchanged

#### Scenario: Clearing a non-member key's accounts is allowed

- **GIVEN** a non-member key has assigned accounts
- **WHEN** its assigned accounts are updated to an empty list
- **THEN** the system SHALL clear its assignments and disable account assignment scoping
