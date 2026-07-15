## ADDED Requirements

### Requirement: Session analytics view visualizes per-session usage

When the Sessions page is opened with a session deep link, the dashboard MUST
render a full-width analytics view for that session containing: summary stat
tiles (duration, requests, tokens with cached share, cost, errors), a stacked
time-series chart of token usage by model, a seat breakdown (donut plus table
grouped by model and reasoning effort, with human seat labels falling back to
the raw model), latency and tokens-per-request distribution charts, and the
recent requests table. Charts MUST follow the dashboard's established chart
theming and reuse the shared chart components where they exist.

#### Scenario: Analytics view renders for a deep-linked session

- **WHEN** the Sessions page loads with a `session` query parameter for a
  known session
- **THEN** the analytics view renders tiles, timeline, seat breakdown, and
  distribution charts populated from the analytics endpoint

#### Scenario: Seat labels map the canonical lineup

- **WHEN** the seat breakdown includes `gpt-5.6-sol` entries at `medium` and
  `xhigh` effort
- **THEN** they are labeled as distinct seats (implementer / verifier) while
  unknown models fall back to their raw model name

### Requirement: Sessions list shows activity sparklines

Sessions list rows MUST render a compact request-activity sparkline from the
list endpoint's sparkline series.

#### Scenario: Sparkline reflects request distribution

- **WHEN** a session's requests cluster in part of the selected window
- **THEN** its row sparkline shows correspondingly uneven activity
