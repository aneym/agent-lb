## ADDED Requirements

### Requirement: Legacy cancellation-pending rows remain compatible

The account API SHALL normalize legacy stored local subscription status
`cancel_pending` to `active` before returning account summaries. Routing
eligibility SHALL continue to treat only local subscription status `canceled` as
subscription-unusable.

#### Scenario: Legacy cancellation-pending row is returned as active

- **GIVEN** an account row still stores local subscription status
  `cancel_pending`
- **WHEN** `/api/accounts` returns that account
- **THEN** the response subscription status is `active`

#### Scenario: Only canceled subscription blocks selection

- **GIVEN** one otherwise eligible account has local subscription status
  `canceled`
- **AND** another otherwise eligible account has a non-canceled local
  subscription status
- **WHEN** account selection builds candidates for routing
- **THEN** only the `canceled` account is excluded by the subscription status
  gate
