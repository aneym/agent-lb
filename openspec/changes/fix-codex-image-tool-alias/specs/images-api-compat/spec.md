## ADDED Requirements

### Requirement: Codex image-tool paths use canonical Images API handlers
POST requests to `/backend-api/codex/images/generations` and `/backend-api/codex/images/edits`, including their duplicated `/backend-api/codex/v1/images/` forms, SHALL reach the corresponding `/v1/images/` handlers without changing auth, validation, body, query, accounting or response behavior. Other tool and WebSocket paths SHALL NOT be rewritten as image requests.

#### Scenario: Image tool invokes an aliased endpoint
- **WHEN** Codex sends an image request to either supported image-tool alias
- **THEN** the canonical Images API handler receives the request with its original body and query
- **AND** the handler enforces the same auth and validation as a direct `/v1/images/` request
