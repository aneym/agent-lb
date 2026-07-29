# frontend-architecture

## ADDED Requirements

### Requirement: Dashboard hides the 5-hour donut for the Codex scope

The dashboard usage donuts MUST omit the 5-Hour Credits donut when the
provider filter is Codex (openai), rendering the Weekly Credits donut alone.
Other provider filters keep both donuts.

#### Scenario: Codex provider filter

- **WHEN** the dashboard provider filter is set to Codex
- **THEN** only the Weekly Credits donut renders

#### Scenario: All-providers filter

- **WHEN** the dashboard provider filter is set to All
- **THEN** both the 5-Hour Credits and Weekly Credits donuts render
