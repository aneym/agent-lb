## ADDED Requirements

### Requirement: GPT-5.6 Sol bootstrap availability
The bundled model catalog SHALL advertise canonical `gpt-5.6-sol` with high reasoning support for eligible paid plans before the first successful live registry refresh, and a live refresh SHALL replace bootstrap metadata without silently substituting another slug.

#### Scenario: Offline startup
- **WHEN** agent-lb starts before a live model registry refresh succeeds
- **THEN** `gpt-5.6-sol` is discoverable and eligible account routing can evaluate its plan support

#### Scenario: Live registry refresh
- **WHEN** live metadata for `gpt-5.6-sol` is fetched
- **THEN** the canonical slug remains `gpt-5.6-sol` and live metadata becomes authoritative
