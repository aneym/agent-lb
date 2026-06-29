## ADDED Requirements

### Requirement: GLM API-key accounts are imported without OAuth
The accounts API SHALL allow an authenticated dashboard/operator caller to import a GLM account from an API key without running an OAuth flow. The imported account SHALL use provider `glm`, SHALL store token material encrypted at rest, SHALL not require an `id_token`, and SHALL default its plan type to `glm-coding` when the caller does not provide a specific GLM plan label.

#### Scenario: Import GLM API key
- **WHEN** a caller imports provider `glm` with a non-empty API key
- **THEN** the system persists an active account with provider `glm`
- **AND** the API key is encrypted into the account's upstream token material
- **AND** no plaintext API key is returned in the response

#### Scenario: Audit omits GLM API key material
- **WHEN** GLM API-key import succeeds
- **THEN** the account-created audit detail identifies the account and provider
- **AND** it does not include the raw API key or decrypted token material

#### Scenario: Non-GLM API-key account import is rejected
- **WHEN** a caller submits the API-key account import endpoint for a provider other than `glm`
- **THEN** the system rejects the request without storing token material
