## ADDED Requirements

### Requirement: Repeated identical compaction turns open a local circuit breaker

The proxy MUST fingerprint `/backend-api/codex/responses` requests whose input
contains a `compaction_trigger` item (fingerprint derived from the model and
the request input) and MUST reject further requests bearing the same
fingerprint with HTTP 429 and a `Retry-After` header once more than five such
requests have been admitted within a ten-minute sliding window. The rejection
MUST happen before an upstream account is selected or consumed. Requests with
distinct fingerprints, and non-compaction requests, MUST be unaffected. The
tracking state MUST be bounded in size and expire with the window. When the
breaker opens for a fingerprint, the proxy MUST record an audit event at most
once per window for that fingerprint.

#### Scenario: identical compaction turn repeats beyond the threshold

- **GIVEN** six identical compaction requests (same model and input) arriving
  within ten minutes
- **WHEN** the sixth request is admitted
- **THEN** it is rejected locally with HTTP 429 and a `Retry-After` header and
  no upstream account is consumed for it, and an audit event records the loop

#### Scenario: distinct compaction turns are not throttled

- **GIVEN** several compaction requests whose inputs differ
- **WHEN** they arrive within the same window
- **THEN** each is admitted normally

#### Scenario: the window expires and the breaker closes

- **GIVEN** a fingerprint that previously opened the breaker
- **WHEN** more than the window duration passes without that fingerprint
- **THEN** a new request with that fingerprint is admitted normally

#### Scenario: non-compaction traffic is never fingerprinted

- **GIVEN** a streaming request whose input has no `compaction_trigger` item
- **WHEN** it repeats identically many times within the window
- **THEN** the breaker never rejects it
