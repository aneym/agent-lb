# api-keys Specification

## Purpose

Define API key lifecycle, enforcement, accounting, and dashboard management contracts for downstream clients.

Requirements continue in these capability specs:

- [api-keys-limits](../api-keys-limits/spec.md): limits, reservations, service-tier enforcement and model allowlists
- [api-keys-usage-accounting](../api-keys-usage-accounting/spec.md): cost accounting, pricing and usage reporting

## Requirements

### Requirement: API Key creation

The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowed_models` (optional list), `weekly_token_limit` (optional integer), `expires_at` (optional ISO 8601 datetime), and `assigned_account_ids` (optional list). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt`, normalize them to UTC naive for persistence, and return the expiration as UTC in API responses.

When `assigned_account_ids` is omitted or empty, the created key SHALL remain unscoped and apply to all accounts. When `assigned_account_ids` is provided with one or more valid account IDs, the created key SHALL enable account-assignment scope and persist those assignments.

#### Scenario: Create unscoped key without assigned accounts

- **WHEN** admin submits `POST /api/api-keys` without `assignedAccountIds`
- **THEN** the created key returns `accountAssignmentScopeEnabled = false`
- **AND** `assignedAccountIds = []`

#### Scenario: Create scoped key with assigned accounts

- **WHEN** admin submits `POST /api/api-keys` with `assignedAccountIds` containing valid account IDs
- **THEN** the created key returns `accountAssignmentScopeEnabled = true`
- **AND** `assignedAccountIds` matches the supplied accounts

#### Scenario: Reject unknown assigned account IDs on create

- **WHEN** admin submits `POST /api/api-keys` with an unknown account ID in `assignedAccountIds`
- **THEN** the system returns 400

#### Scenario: Create key and show plain key

- **WHEN** admin submits `POST /api/api-keys` with a valid payload
- **THEN** the response contains the full plain key exactly once and the system never returns the plain key on subsequent reads

#### Scenario: Create key with timezone-aware expiration

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

### Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence.

#### Scenario: Update key with timezone-aware expiration
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

#### Scenario: Update non-existent key

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

### Requirement: API Key deletion

The system SHALL allow deleting an API key via `DELETE /api/api-keys/{id}`. Deletion MUST be permanent and the key MUST immediately stop authenticating.

#### Scenario: Delete existing key

- **WHEN** admin calls `DELETE /api/api-keys/{id}` for an existing key
- **THEN** the key is permanently removed from the database and returns 204

#### Scenario: Delete non-existent key

- **WHEN** admin calls `DELETE /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

### Requirement: API Key regeneration

The system SHALL allow regenerating an API key via `POST /api/api-keys/{id}/regenerate`. This MUST generate a new key value (new hash, new prefix) while preserving all other properties (name, models, limits, expiration). The new plain key MUST be returned exactly once.

#### Scenario: Regenerate key

- **WHEN** admin calls `POST /api/api-keys/{id}/regenerate`
- **THEN** the system returns the updated key object with a new `key` and `keyPrefix`; the old key immediately stops authenticating

### Requirement: API Key authentication global switch
The system SHALL provide an `api_key_auth_enabled` boolean in `DashboardSettings`. When false (default), local requests to protected proxy routes MAY proceed without an API key. Operators MAY additionally opt specific non-local proxy clients into unauthenticated access by configuring `proxy_unauthenticated_client_cidrs`. Requests that are neither local nor explicitly allowlisted MUST be rejected until proxy authentication is configured. When true, protected proxy routes require a valid API key in the `Authorization` header using the Bearer authentication scheme.

#### Scenario: Enable API key auth

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": true }`
- **THEN** subsequent proxy requests without a valid Bearer token are rejected with 401

#### Scenario: Disable API key auth for a local request

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a local client calls a protected proxy route
- **THEN** the request is allowed without API key authentication

#### Scenario: Disable API key auth for a non-local request

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a non-local client calls a protected proxy route
- **THEN** the request is rejected with 401 until proxy authentication is configured

#### Scenario: Disable API key auth for an explicitly allowlisted proxy client
- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** the request socket peer IP belongs to configured `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy route proceeds without API key authentication

#### Scenario: Disable API key auth for a non-local request outside the explicit allowlist
- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a non-local client calls a protected proxy route
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the request is rejected with 401 until proxy authentication is configured

#### Scenario: Enable without any keys created

- **WHEN** admin enables API key auth but no keys exist
- **THEN** all proxy requests are rejected with 401 (the system SHALL NOT prevent enabling even if no keys exist)

#### Scenario: Toggle API key auth

- **WHEN** admin toggles `apiKeyAuthEnabled` in settings
- **THEN** the system calls `PUT /api/settings` and reflects the new state

### Requirement: API Key Bearer authentication guard
The system SHALL validate API keys on protected proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`.

`/api/codex/usage` SHALL NOT be covered by the API key auth guard scope.

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: Disabled auth allowlist uses raw socket peer only
- **WHEN** `api_key_auth_enabled` is false
- **AND** forwarded headers claim a different client IP
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the dependency rejects the request with 401
- **AND** forwarded headers do not satisfy the explicit allowlist

#### Scenario: API key guard route scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/v1/responses`, `/backend-api/codex/responses`, `/v1/audio/transcriptions`, or `/backend-api/transcribe`
- **THEN** the API key guard validation is applied

#### Scenario: Codex usage excluded from API key guard scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key guard validation is not applied

#### Scenario: Valid API key injected into handler

- **WHEN** `api_key_auth_enabled` is true and a valid Bearer token is provided
- **THEN** the route handler receives a typed `ApiKeyData` parameter (not `request.state`)

#### Scenario: API key auth disabled returns None for local requests

- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as local
- **THEN** the dependency returns `None` and the request proceeds without authentication

#### Scenario: API key auth disabled rejects non-local requests

- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as non-local
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the dependency rejects the request with 401

### Requirement: Model restriction enforcement

The system SHALL enforce per-key model restrictions in the proxy service layer (not middleware). When `allowed_models` is set (non-null, non-empty) and the requested model is not in the list, the system MUST reject the request. The `/v1/models` endpoint MUST filter the model list based on the authenticated key's `allowed_models`.

For fixed-model endpoints such as `/v1/audio/transcriptions` and `/backend-api/transcribe`, the service MUST evaluate restrictions against fixed effective model `gpt-4o-transcribe`.

`/backend-api/codex/models` SHALL keep the existing allowlist filtering behavior by default. When an authenticated API key has `apply_to_codex_model = true` and `allowed_models` is non-empty, `/backend-api/codex/models` SHALL return the full catalog and rewrite each model entry visibility so allowlisted models use `visibility: "list"` and every other model uses `visibility: "hide"`. When `apply_to_codex_model = true` but `allowed_models` is null or empty, `/backend-api/codex/models` SHALL preserve the original behavior because there is no allowlist to apply.

#### Scenario: Requested model not allowed

- **WHEN** a key has `allowed_models: ["o3-pro"]` and a request is made for model `gpt-4.1`
- **THEN** the proxy returns 403 with OpenAI-format error `{ "error": { "code": "model_not_allowed", "message": "This API key does not have access to model 'gpt-4.1'" } }`

#### Scenario: Cursor alias allowed model permits canonical request

- **WHEN** a key has `allowed_models: ["gpt-5.4-mini-high"]`
- **AND** a request is made for model `gpt-5.4-mini`
- **THEN** the proxy permits the request because the allowed alias resolves to the requested canonical model

#### Scenario: All models allowed

- **WHEN** a key has `allowed_models: null`
- **THEN** any model is permitted

#### Scenario: Model list filtered

- **WHEN** a key with `allowed_models: ["o3-pro"]` calls `GET /v1/models`
- **THEN** the response contains only models matching the allowed list

#### Scenario: Model list canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]` and `enforced_model: "gpt-5.4-mini-high"` calls `GET /v1/models`
- **THEN** the response contains the canonical model `gpt-5.4-mini`
- **AND** the response does not expose a synthetic `gpt-5.4-mini-high` model id

#### Scenario: Codex model list visibility canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]`, `enforced_model: "gpt-5.4-mini-high"`, and `apply_to_codex_model=true` calls `GET /backend-api/codex/models`
- **THEN** the canonical `gpt-5.4-mini` entry is visible with `visibility: "list"`
- **AND** other entries are hidden according to the API key allowlist policy

#### Scenario: No API key auth (disabled)

- **WHEN** `api_key_auth_enabled` is false and a request is made to `/v1/models`
- **THEN** the full model catalog is returned

#### Scenario: Fixed transcription model not allowed

- **WHEN** a key has `allowed_models: ["gpt-5.1"]` and a request is made to `/v1/audio/transcriptions` or `/backend-api/transcribe`
- **THEN** the proxy returns 403 with OpenAI-format error code `model_not_allowed` for model `gpt-4o-transcribe`

#### Scenario: Codex models keep filtered behavior by default
- **WHEN** a key has `allowed_models: ["o3-pro"]` and `apply_to_codex_model: false`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response contains only models matching the allowed list

#### Scenario: Codex models rewrite visibility when opted in
- **WHEN** a key has `allowed_models: ["o3-pro"]` and `apply_to_codex_model: true`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response contains the full catalog
- **AND** the `o3-pro` entry has `visibility: "list"`
- **AND** every model not in `allowed_models` has `visibility: "hide"`

#### Scenario: Codex models preserve original behavior without an allowlist
- **WHEN** a key has `allowed_models: null` and `apply_to_codex_model: true`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response preserves the original `/backend-api/codex/models` behavior because there is no allowlist to apply

### Requirement: Weekly token usage tracking

The system SHALL atomically increment `weekly_tokens_used` on the API key record when a proxy request completes with token usage data. The token count MUST be `input_tokens + output_tokens`. If token usage is unavailable (error response), the counter MUST NOT be incremented.

#### Scenario: Successful request with usage

- **WHEN** a proxy request completes with `input_tokens: 100, output_tokens: 50` for an authenticated key
- **THEN** `weekly_tokens_used` is atomically incremented by 150

#### Scenario: Request with no usage data

- **WHEN** a proxy request fails with an error and no usage data is returned
- **THEN** `weekly_tokens_used` is not incremented

#### Scenario: Request without API key auth

- **WHEN** `api_key_auth_enabled` is false and a proxy request completes
- **THEN** no API key usage tracking occurs

### Requirement: RequestLog API key reference

The system SHALL record the `api_key_id` in the `request_logs` table for proxy requests authenticated with an API key. The field MUST be NULL when API key auth is disabled or the request is unauthenticated.

#### Scenario: Authenticated request logged

- **WHEN** a proxy request is authenticated with API key `key-123` and completes
- **THEN** the `request_logs` entry has `api_key_id = "key-123"`

#### Scenario: Unauthenticated request logged

- **WHEN** API key auth is disabled and a proxy request completes
- **THEN** the `request_logs` entry has `api_key_id = NULL`

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, assigned-account selection, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again, and the copy action MUST remain functional in secure and non-secure contexts.

The create and edit dialogs SHALL expose an `Apply to codex /model` checkbox directly below `Allowed models`. The checkbox SHALL default to unchecked for new keys and SHALL edit the stored API key value for existing keys.

#### Scenario: Create key with optional account scoping

- **WHEN** an admin opens the create API key dialog
- **THEN** the dialog shows the Assigned accounts picker
- **AND** leaving the picker at `All accounts` creates an unscoped key
- **AND** selecting one or more accounts creates a scoped key for only those accounts

#### Scenario: Create key and show plain key

- **WHEN** admin creates a key via the UI
- **THEN** a dialog shows the full plain key with a copy button and a warning message

#### Scenario: API key dialog copy fallback

- **WHEN** a user clicks Copy for the created API key inside the dialog
- **THEN** the copy operation succeeds using secure Clipboard API when available
- **AND** falls back to dialog-scoped `execCommand("copy")` when secure Clipboard API is unavailable

#### Scenario: Create key with codex model visibility option
- **WHEN** an admin opens the create API key dialog
- **THEN** the `Apply to codex /model` checkbox appears directly below `Allowed models`
- **AND** it is unchecked by default

#### Scenario: Edit key with stored codex model visibility option
- **WHEN** an admin opens the edit API key dialog for a key with `apply_to_codex_model: true`
- **THEN** the `Apply to codex /model` checkbox is shown as checked
