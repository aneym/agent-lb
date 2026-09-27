## ADDED Requirements

### Requirement: Dashboard recent requests display identity with provenance

The recent-requests list SHALL render the request-log caller user and machine
handles and distinguish machine provenance, including self-reported `claimed`
from resolved `tailnet` and `local`. Historical rows whose identity fields are
null MUST remain readable without inventing an owner. The view MUST NOT show
email, login, or raw client IP as identity.

#### Scenario: Claimed machine is visibly different

- **GIVEN** a recent request with machine `box-1` and source `claimed`
- **WHEN** the dashboard renders the request list
- **THEN** the machine is identified as self-reported, not verified tailnet

#### Scenario: Old log has no inferred identity

- **WHEN** the dashboard renders a log with null caller identity fields
- **THEN** it shows unknown or empty attribution without guessing a person
