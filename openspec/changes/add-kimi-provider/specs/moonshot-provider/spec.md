# moonshot-provider

## ADDED Requirements

### Requirement: Moonshot provider registration
agent-lb SHALL register a `moonshot` provider that reuses the Anthropic
Messages request/response handling (no schema translation) and supports two
account kinds: platform API-key accounts and subscription OAuth accounts.

#### Scenario: provider is registered
- **WHEN** the provider registry is loaded
- **THEN** `moonshot` resolves to a provider whose Messages handling is
  Anthropic-native

### Requirement: Account-kind upstream selection
Moonshot API-key accounts SHALL be forwarded to the configured platform
Anthropic-compatible base URL. Moonshot subscription accounts SHALL be
forwarded to the configured subscription base URL. Both base URLs SHALL be
configurable via settings.

#### Scenario: api-key account uses platform upstream
- **GIVEN** an active Moonshot API-key account
- **WHEN** a `kimi-k3` Messages request selects it
- **THEN** the request is forwarded to the platform Anthropic upstream with
  the account's key as bearer

#### Scenario: subscription account uses coding upstream
- **GIVEN** an active Moonshot subscription account
- **WHEN** a `kimi-k3` Messages request selects it
- **THEN** the request is forwarded to the subscription upstream with the
  account's OAuth access token as bearer

### Requirement: Coding-agent identification headers on subscription requests
Upstream requests authenticated with a subscription OAuth token SHALL include
the coding-agent identification headers (`X-Msh-Platform`, `X-Msh-Version`,
`X-Msh-Device-Name`, `X-Msh-Device-Model`, `X-Msh-Os-Version`,
`X-Msh-Device-Id`) with a device id that is stable per account across restarts.

#### Scenario: fingerprint headers present
- **WHEN** a subscription-account request is forwarded upstream
- **THEN** all `X-Msh-*` identification headers are present
- **AND** `X-Msh-Device-Id` matches the device id stored with the account

### Requirement: Status-specific failure cooldowns
Moonshot upstream failures SHALL map to status-specific cooldown windows:
auth failures (401) and payment/permission failures (402/403) to a long fixed
window; 429 quota failures to an exponential backoff that honors an upstream
`Retry-After` hint when present; transient 5xx failures to a short
configurable window. Additional failures arriving while a cooldown window is
already open SHALL reuse the open window instead of escalating backoff again.

#### Scenario: 429 honors Retry-After
- **WHEN** the upstream returns 429 with `Retry-After: 120`
- **THEN** the account cools down approximately 120 seconds rather than the
  computed backoff

#### Scenario: burst does not re-escalate
- **GIVEN** an account already in an open 429 cooldown window
- **WHEN** additional in-flight requests fail for the same account
- **THEN** the backoff level advances at most once for that window

### Requirement: Kimi request-shape compatibility
The forwarding path for Moonshot SHALL NOT emit upstream requests that Kimi
rejects: assistant tool-call history entries missing reasoning content when
thinking is enabled, content arrays containing empty-string text parts, and
tool-result blocks whose `tool_call_id` does not match a prior tool call.

#### Scenario: empty text part filtered
- **WHEN** a client Messages payload contains an empty-string text content part
- **THEN** the upstream Moonshot request does not contain that empty part

### Requirement: No hardcoded exhaustive model catalog
Moonshot routing SHALL be driven by the `kimi-` model prefix. Unknown
`kimi-*` model ids SHALL route to Moonshot accounts without requiring a
catalog update or redeploy.

#### Scenario: new model id routes by prefix
- **WHEN** a client requests model `kimi-k3.1-preview` unknown to the catalog
- **THEN** the request still selects Moonshot accounts and forwards upstream
