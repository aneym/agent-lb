# claude-harness-codex delta

## MODIFIED Requirements

### Requirement: Worker model aliases on the Messages route

The ordinary `/v1/messages` route MUST serve a request whose model names a
recognized Sol worker alias — `gpt-5.6-sol` or `gpt-5.6-sol-<effort>` for
efforts `low`, `medium`, `high`, `xhigh` — through the CCGPT compatibility
bridge rather than the Anthropic account pool. An effort-suffixed alias MUST
pin that reasoning effort regardless of the request's own effort value; the
plain `gpt-5.6-sol` alias defers to the request's `output_config.effort` and
falls back to the bridge default. The locked Sol model and `priority`
service tier apply to every alias request. `/v1/messages/count_tokens` for
an alias model MUST return a conservative local estimate without selecting
or calling an Anthropic account. The dedicated compatibility route is
`/v1/ccgpt/messages` (renamed from `/v1/ccdex/messages`; no compatibility
alias is retained for the old path).

#### Scenario: Effort-pinned alias

- **WHEN** `/v1/messages` receives a request with model `gpt-5.6-sol-xhigh`
- **THEN** the translated Responses request uses model `gpt-5.6-sol`, effort `xhigh`, and service tier `priority`
- **AND** the caller receives Anthropic-shaped output

#### Scenario: Plain alias defers to request effort

- **WHEN** `/v1/messages` receives model `gpt-5.6-sol` with `output_config.effort` of `medium`
- **THEN** the translated Responses request uses effort `medium`

#### Scenario: Alias token counting stays local

- **WHEN** `/v1/messages/count_tokens` receives an alias model
- **THEN** it returns a local estimate and no Anthropic account is selected

#### Scenario: Dedicated route renamed

- **WHEN** a client posts to `/v1/ccgpt/messages`
- **THEN** the request is served through the compatibility bridge exactly as `/v1/ccdex/messages` was before this change
- **AND** a request to `/v1/ccdex/messages` returns 404
