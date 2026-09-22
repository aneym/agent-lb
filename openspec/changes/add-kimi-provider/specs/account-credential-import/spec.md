## ADDED Requirements

### Requirement: Kimi accounts are imported from API keys or OAuth token bundles
The accounts API SHALL allow an authenticated dashboard/operator caller to import a Kimi account through the API-key import endpoint without running an interactive OAuth flow. The endpoint SHALL accept provider `kimi` with either a static API key alone or an OAuth bundle (access token plus refresh token). Imported token material SHALL be stored encrypted at rest, SHALL not require an `id_token`, and SHALL default plan type, email, alias, and account id to Kimi-specific values resolved from the provider's import profile when the caller omits them.

#### Scenario: Import Kimi OAuth token bundle
- **WHEN** a caller imports provider `kimi` with a non-empty access token and a non-empty refresh token
- **THEN** the system persists an active account with provider `kimi`
- **AND** the access and refresh tokens are encrypted into the account's upstream token material
- **AND** no plaintext token material is returned in the response or audit detail

#### Scenario: Import Kimi static API key
- **WHEN** a caller imports provider `kimi` with only an API key
- **THEN** the system persists an active account whose refresh material equals the API key

#### Scenario: Unsupported provider is still rejected
- **WHEN** a caller submits the API-key import endpoint for a provider that does not declare API-key import support (e.g. `openai`)
- **THEN** the system rejects the request without storing token material

### Requirement: GLM import behavior is preserved by the generalization
Generalizing the API-key import endpoint SHALL NOT change the GLM import contract: provider `glm` imports SHALL keep their existing defaults (plan `glm-coding`, GLM email/alias/account-id defaults) and validation behavior.

#### Scenario: GLM import unchanged
- **WHEN** a caller imports provider `glm` with only an API key
- **THEN** the persisted account matches the pre-change GLM import contract

#### Scenario: Static-API-key providers reject a separate refresh token
- **WHEN** a caller supplies `refreshToken` for a provider whose credentials are a static API key (e.g. `glm`)
- **THEN** the system rejects the request without storing token material

#### Scenario: Explicitly null alias leaves the alias unset
- **WHEN** a caller imports with an explicitly null `alias`
- **THEN** the stored alias is left unset rather than taking the provider default
- **AND** an omitted `alias` still takes the provider default
