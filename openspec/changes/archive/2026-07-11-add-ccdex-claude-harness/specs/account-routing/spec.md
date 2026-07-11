## ADDED Requirements

### Requirement: Ccdex provider ownership
Agent-lb MUST route every authenticated `ccdex` compatibility request only through eligible OpenAI accounts and MUST preserve existing OpenAI session/cache affinity, failover, settlement, and API-key policy behavior.

#### Scenario: Mixed provider pool
- **WHEN** both OpenAI and Anthropic accounts are eligible and a `ccdex` request arrives
- **THEN** the selected account is an OpenAI account and no Anthropic account receives the request

#### Scenario: OpenAI pool unavailable
- **WHEN** no eligible OpenAI account can serve GPT-5.6 Sol
- **THEN** the request fails with an Anthropic-native error and does not fall back across provider ownership

### Requirement: Ccdex credential isolation
The compatibility route MUST discard inbound Claude authorization credentials before OpenAI forwarding and MUST apply normal agent-lb authentication and model/tier/usage policies.

#### Scenario: Inbound Claude credential
- **WHEN** a compatibility request includes Claude OAuth or API-key headers
- **THEN** those credentials are not forwarded to OpenAI and are not written to logs
