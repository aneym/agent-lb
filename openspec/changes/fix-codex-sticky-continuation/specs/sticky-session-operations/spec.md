## ADDED Requirements

### Requirement: Rejected websocket anchors recover without foreign lineage

The service SHALL classify the backend error with type invalid_request_error and exact message Invalid `previous_response_id`. as continuity loss even when code and param are omitted. It SHALL use existing bounded full-history recovery or emit the explicit continuity error when history is insufficient.

#### Scenario: Full resend receives an unclassified backend invalid anchor

- **GIVEN** a retry-safe full resend and no downstream output
- **WHEN** upstream rejects the anchor with the exact message and no code or param
- **THEN** the service retries at most once without the anchor using retained full input
- **AND** it does not return the raw fatal invalid-request frame

#### Scenario: Short continuation cannot reconstruct history

- **GIVEN** a continuation that depends on the stored response
- **WHEN** its anchor is rejected
- **THEN** the service returns an explicit continuity error without replaying incomplete input

### Requirement: Verified websocket full resends may rotate after quota rejection

After a pre-created quota rejection, the service MAY rotate a verified full resend that has no file dependency. It MUST remove previous_response_id and the old account preference before selection, exclude the rejected account, preserve request settlement ownership and obey the existing single-replay bound. Client full resends MUST match known completed history; multiple input items alone do not prove cross-account replay safety.

#### Scenario: Connected owner rejects a verified full resend

- **GIVEN** an unstarted full-history request verified against the session or retained before proxy anchor injection
- **WHEN** its account rejects it for quota
- **THEN** the router updates account health and selects another eligible account with the full input and no old anchor
- **AND** the request reservation settles only after the final outcome

#### Scenario: File or visible-output dependency prevents rotation

- **GIVEN** an explicit file pin, input file/image reference, visible output, started response, or consumed replay
- **WHEN** quota rejects the request
- **THEN** the service does not transparently rotate that request

#### Scenario: Short Codex continuation requests client-side reset on owner quota rejection

- **GIVEN** a pre-created Codex continuation whose retained input cannot safely be replayed without its anchor
- **AND** it has no known file pin, file/image reference or conversation dependency
- **WHEN** its owner rejects the request for quota
- **THEN** the service returns codex_previous_response_stale with a full-history resend instruction
- **AND** the proxy does not forward the incomplete delta to another account
- **AND** generic Responses clients retain upstream_unavailable
