## ADDED Requirements

### Requirement: Cursor backup is blocked before entitlement

agent-lb MUST expose a Cursor provider-native backup candidate whose observed onboarding states are `discovered` then `blocked`, whose disposition is `blocked`, and whose `enabled` flag is false. The candidate MUST name `execution_mode=provider_native` and MUST NOT be dispatch-admitted. Catalog discovery and usage reconciliation MUST be uninvoked, the model list MUST be empty, entitlement MUST be `unknown`, `meter_mode` MUST be `provider_managed`, numeric headroom MUST be `NOT_EXPOSED`, and admission MUST be `NATIVE_MANAGED_BLOCKED`. The candidate MUST NOT copy a browser session, hold credentials, publish an account reference, or spend API credit.

#### Scenario: Headless login cannot complete

- **GIVEN** no completed official Cursor consent
- **WHEN** the Cursor native-backup record is read
- **THEN** it is blocked, not enabled, and its consent action is exactly `agent login`

#### Scenario: Unknown entitlement is not inferred into admission

- **GIVEN** the Cursor record has unknown entitlement and unverified inclusion/overage
- **WHEN** dispatch admission is evaluated, even if the disposition label is changed to enabled
- **THEN** admission stays false

### Requirement: Grok stays disabled

agent-lb MUST expose a Grok record with disposition `disabled` and `enabled` false. The record MUST NOT spend xAI API credit, MUST NOT mark a subscription entitlement, and MUST NOT be dispatch-admitted.

#### Scenario: No believed subscription

- **GIVEN** the owner does not believe a Grok subscription exists
- **WHEN** the Grok native-backup record is read
- **THEN** it is disabled, has no consent packet, and does not spend API credit

### Requirement: Backup records stay out of the live provider registry

Cursor and Grok MUST both appear in the native-backup list and MUST be rejected by live provider lookup. Importing the backup module MUST NOT register either name.

#### Scenario: Live lookup rejects both names

- **GIVEN** the native-backup module is imported
- **WHEN** live provider lookup is asked for `cursor` or `grok`
- **THEN** lookup fails and the live provider list is unchanged
