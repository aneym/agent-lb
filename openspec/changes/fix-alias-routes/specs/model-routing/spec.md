## ADDED Requirements

### Requirement: Model policy covers proxy payloads
The proxy SHALL resolve configured aliases and reject excluded requested models or resolved targets before dispatch on every registered proxy route with a model field. Multipart rewriting SHALL preserve uploaded bytes and unrelated fields.

#### Scenario: Multipart alias
- **WHEN** an image edit or transcription request includes a configured alias in its multipart model field
- **THEN** the handler receives its resolved target and the original uploaded bytes

#### Scenario: Excluded model on additional routes
- **WHEN** an image edit, transcription, backend transcription or warmup payload names an excluded model or an alias targeting one
- **THEN** the proxy returns HTTP 400 with model_not_allowed before invoking the handler
