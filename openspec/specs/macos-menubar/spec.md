# macOS Menu-bar Specification

## Purpose

Define the operator-visible behavior and state semantics of the native macOS
menu-bar client.

## Requirements

### Requirement: Menu-bar section errors represent completed fetch failures

The macOS menu-bar client MUST show a section retry state only when the most
recent completed fetch for that section failed. Cancelling an in-flight fetch
because the popover closed or restarted MUST NOT create or clear the section's
error state. A successful foreground or silent background fetch MUST clear an
older error for the same section.

#### Scenario: Closing the popover cancels healthy in-flight fetches

- **WHEN** pool, accounts, or recent fetches are cancelled by popover lifecycle
- **THEN** the client does not add a visible error for those sections
- **AND** cached section data can render without a misleading retry row

#### Scenario: Silent refresh repairs a stale error

- **GIVEN** a section has a recorded error from a genuine completed failure
- **WHEN** its closed-state silent fetch later succeeds
- **THEN** the client clears that section error

#### Scenario: Genuine failure with stale data remains visible

- **WHEN** a non-cancellation section fetch fails after stale data already exists
- **THEN** the client preserves the data and shows the retry affordance
