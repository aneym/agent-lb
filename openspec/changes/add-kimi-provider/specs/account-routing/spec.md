## ADDED Requirements

### Requirement: Kimi Messages routing uses the Kimi provider pool
Anthropic-compatible Messages requests whose canonical model starts with `kimi-` or `k3` SHALL select only active, eligible Kimi provider accounts. The proxy SHALL forward those requests to the configured Kimi Anthropic-compatible upstream base URL (default `https://api.kimi.com/coding`) and SHALL inject the selected Kimi account bearer token upstream. Existing Anthropic/Claude and GLM model requests SHALL continue selecting only their own provider pools.

#### Scenario: Kimi model selects Kimi account
- **GIVEN** one active Anthropic account, one active GLM account, and one active Kimi account
- **WHEN** a client sends `/v1/messages` with model `k3-256k`
- **THEN** agent-lb selects the Kimi account
- **AND** forwards the request to the Kimi Anthropic-compatible upstream
- **AND** records the request log provider as `kimi`

#### Scenario: Claude and GLM models do not select Kimi accounts
- **GIVEN** active Anthropic, GLM, and Kimi accounts
- **WHEN** a client sends `/v1/messages` with a Claude model or a `glm-` model
- **THEN** agent-lb selects only from that model's own provider pool

### Requirement: Kimi quota cooldowns are provider-scoped
Kimi Messages routing SHALL classify Kimi requests into Kimi quota keys (with a distinct thinking variant) and SHALL persist upstream Kimi rate-limit cooldown evidence under those keys. Kimi cooldowns SHALL NOT block Anthropic/Claude or GLM Messages routing, and vice versa. When every Kimi account is cooling down for the requested quota key, agent-lb SHALL return a structured retryable no-available-Kimi-accounts error with reset evidence when known.

#### Scenario: Kimi thinking request uses thinking quota key
- **WHEN** a Kimi Messages request includes thinking
- **THEN** agent-lb uses the Kimi thinking quota key for selection and cooldown checks

#### Scenario: Kimi cooldown returns Kimi error code
- **GIVEN** every Kimi account is cooling down for the requested Kimi quota key
- **WHEN** a client sends a Kimi Messages request
- **THEN** agent-lb returns a structured retryable no-available-Kimi-accounts error

### Requirement: Count-tokens is served locally for providers without upstream support
Providers whose profile declares no upstream `count_tokens` support SHALL have `/v1/messages/count_tokens` answered from the local estimator instead of being forwarded upstream. Providers that do support it SHALL continue forwarding the request and returning the upstream envelope verbatim.

#### Scenario: Kimi count-tokens is answered locally
- **WHEN** a client sends `/v1/messages/count_tokens` with a Kimi model
- **THEN** agent-lb returns a locally estimated `input_tokens`
- **AND** no upstream count-tokens request is made

#### Scenario: Anthropic and GLM count-tokens still forward
- **WHEN** a client sends `/v1/messages/count_tokens` with a Claude or `glm-` model
- **THEN** agent-lb forwards the request upstream and returns the upstream envelope

### Requirement: Provider routing metadata is table-driven
Per-provider Anthropic-compatible routing metadata (model prefixes, quota keys, count-tokens quota key, sticky prefix, display label, no-accounts and cooldown error codes, upstream base URL source, default probe model, and import defaults) SHALL be resolved from a declarative per-provider profile rather than provider-name conditionals scattered across routing helpers. Adding an Anthropic-compatible provider SHALL NOT change routing behavior for existing providers.

#### Scenario: Existing provider behavior survives the refactor
- **WHEN** the provider profile table is introduced
- **THEN** GLM and Anthropic Messages routing (quota keys, sticky prefixes, error codes, upstream base URLs) behaves exactly as before the change
