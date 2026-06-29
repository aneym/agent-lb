## ADDED Requirements

### Requirement: GLM Messages routing uses the GLM provider pool
Anthropic-compatible Messages requests whose canonical model starts with `glm-` SHALL select only active, eligible GLM provider accounts. The proxy SHALL forward those requests to the configured GLM Anthropic-compatible upstream base URL and SHALL inject the selected GLM account bearer token upstream. Existing Anthropic/Claude model requests SHALL continue selecting only Anthropic provider accounts.

#### Scenario: GLM model selects GLM account
- **GIVEN** one active Anthropic account and one active GLM account
- **WHEN** a client sends `/v1/messages` with model `glm-5.2`
- **THEN** agent-lb selects the GLM account
- **AND** forwards the request to the GLM Anthropic-compatible upstream
- **AND** records the request log provider as `glm`

#### Scenario: Claude model does not select GLM account
- **GIVEN** active Anthropic and GLM accounts
- **WHEN** a client sends `/v1/messages` with a Claude model
- **THEN** agent-lb selects only from Anthropic accounts

### Requirement: GLM quota cooldowns are provider-scoped
GLM Messages routing SHALL classify GLM requests into GLM quota keys and SHALL persist upstream GLM rate-limit cooldown evidence under those keys. GLM cooldowns SHALL NOT block Anthropic/Claude Messages routing, and Anthropic cooldowns SHALL NOT block GLM Messages routing.

#### Scenario: GLM thinking request uses thinking quota key
- **WHEN** a GLM Messages request includes thinking
- **THEN** agent-lb uses the GLM thinking quota key for selection and cooldown checks

#### Scenario: GLM cooldown returns GLM error code
- **GIVEN** every GLM account is cooling down for the requested GLM quota key
- **WHEN** a client sends a GLM Messages request
- **THEN** agent-lb returns a structured retryable no-available-GLM-accounts error with reset evidence when known
