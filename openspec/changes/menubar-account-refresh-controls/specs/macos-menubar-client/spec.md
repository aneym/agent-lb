## ADDED Requirements

### Requirement: Per-account on-demand re-verification from the menubar

The menubar app MUST let the operator force re-verification of a single
account without leaving the panel. Rows whose subscription ledger status is
`canceled` MUST trigger `POST /api/accounts/{accountId}/subscription/check`;
other refreshable rows MUST trigger `POST /api/accounts/{accountId}/probe`.
Paused and disconnected (deactivated / reauth-required) rows MUST NOT surface
the control unless their subscription ledger is canceled. The control MUST be
subtle: revealed on row hover (always visible on unsubscribed rows), with an
in-flight progress state and a brief inline success/failure hint — no modal
dialogs and no layout shifts. After a successful action the app MUST refresh
the account list so the re-verified state renders.

#### Scenario: Unsubscribed row re-checks the subscription ledger

- **GIVEN** an account whose subscription ledger status is `canceled`
- **WHEN** the operator activates the row's refresh control
- **THEN** the client POSTs `/api/accounts/{accountId}/subscription/check`
- **AND** on success the account list is refreshed so a now-working account stops rendering as `unsubscribed`

#### Scenario: Active row probes the upstream

- **GIVEN** an account that is neither subscription-canceled, paused, nor disconnected
- **WHEN** the operator activates the row's refresh control
- **THEN** the client POSTs `/api/accounts/{accountId}/probe`
- **AND** on success the account list is refreshed

#### Scenario: Non-probable rows do not offer a probe

- **WHEN** an account is paused, deactivated, or reauth-required and its subscription ledger is not `canceled`
- **THEN** the row renders no refresh control
- **AND** a probe rejected with HTTP 409 `account_not_probable` (e.g. a state race) surfaces as the row's inline failure hint, not a dialog

#### Scenario: Refresh feedback is inline and transient

- **WHEN** a refresh action is in flight
- **THEN** the control shows a progress indicator in the same fixed-size slot
- **AND** after completion it shows a success or failure glyph for approximately 2 seconds before returning to idle
- **AND** revealing or swapping the control never changes the row layout
