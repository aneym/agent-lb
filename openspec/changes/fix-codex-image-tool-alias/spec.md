# Codex image tool URL compatibility

POST /backend-api/codex/images/generations and /backend-api/codex/images/edits must resolve to the existing /v1/images handlers. The duplicated /backend-api/codex/v1/images forms must resolve identically. Auth, validation, body, query, accounting and response behavior must remain those of the canonical Images API. No arbitrary tool paths or WebSocket paths are mapped to Images endpoints.

Acceptance: alias/canonical invalid-model and missing-image responses agree; middleware preserves auth/query/body and caller scope; existing Images integration tests pass. Live native-tool generation is required before claiming that generation works.
