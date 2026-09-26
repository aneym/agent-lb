## ADDED Requirements

### Requirement: Kimi OAuth tokens refresh through the Kimi token endpoint
For Kimi accounts holding an OAuth refresh token, the provider SHALL refresh access tokens with a form-encoded `refresh_token` grant against the configured Kimi OAuth token URL (default `https://auth.kimi.com/api/oauth/token`) using the configured public Kimi Code client id. A rotated refresh token returned by the grant SHALL be persisted in place of the previous one. For Kimi accounts whose stored refresh material is a static API key, refresh SHALL echo the key without calling the token endpoint. Token values SHALL never be logged.

#### Scenario: OAuth refresh rotates the stored refresh token
- **GIVEN** a Kimi account imported with an OAuth token bundle
- **WHEN** the refresh grant returns a new access token and a new refresh token
- **THEN** both are persisted encrypted and used for subsequent upstream requests

#### Scenario: API-key account refresh is a no-op echo
- **GIVEN** a Kimi account imported with only a static API key
- **WHEN** the provider refresh runs
- **THEN** the access token remains the API key and no token-endpoint call is made
