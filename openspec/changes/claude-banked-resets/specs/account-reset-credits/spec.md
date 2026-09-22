## ADDED Requirements

### Requirement: Claude resets are account-scoped and exhaustion-gated
The service SHALL redeem only the provider-selected eligible, usable, unexpired
grant that clears every currently exhausted window on the selected account.
It SHALL enforce a global rolling 24-hour limit of one Claude redemption.

#### Scenario: Unknown redemption outcome
- **WHEN** a redemption response is lost or indeterminate
- **THEN** durable pending state blocks further redemption until reconciled
- **AND** the service does not report success or spend another credit

#### Scenario: Account still has capacity
- **WHEN** the provider reports the account is not at a limit
- **THEN** the service keeps the reset banked even if early redemption is allowed

### Requirement: Reset inventory is not fabricated
The dashboard and menubar SHALL distinguish a known zero credit count from
unknown, failed, or expired inventory. Counts SHALL be account-specific and
include supported Claude and Codex accounts.

#### Scenario: Inventory request fails
- **WHEN** the inventory cannot be refreshed
- **THEN** the UI reports unknown availability rather than zero credits
