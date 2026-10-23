# anthropic-messages-compat Specification

## Purpose

Define how agent-lb accepts Anthropic Messages requests, including Anthropic-defined server tools, on the passthrough route and the Codex bridge.

## Requirements
### Requirement: Versioned server tools pass through on the Messages route

`POST /v1/messages` MUST accept a `tools` entry whose `type` is present and is
not `custom` (an Anthropic-defined server tool such as `web_search_20250305`)
without an `input_schema`, and MUST forward it to the upstream Messages API with
every field the client sent, unchanged. A tool with no `type` or with
`type: "custom"` MUST still carry an `input_schema` and MUST be rejected at
request validation without one.

#### Scenario: WebSearch server tool reaches upstream unchanged

- **GIVEN** a request whose `tools` contains `{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}`
- **WHEN** a client calls `POST /v1/messages`
- **THEN** the request is not rejected by validation
- **AND** the tool forwarded upstream equals the tool the client sent

#### Scenario: Custom tool without input_schema is rejected

- **GIVEN** a request whose `tools` contains `{"name": "lookup"}` with no `type` and no `input_schema`
- **WHEN** a client calls `POST /v1/messages`
- **THEN** the request fails validation and is not forwarded

### Requirement: Codex bridge fails closed on server tools

`POST /v1/ccdex/messages` MUST NOT drop a server tool it cannot translate. When
any `tools` entry is a server tool, the route MUST respond `400` with an
Anthropic error envelope of type `invalid_request_error` whose message names
each server tool type, and MUST NOT call the upstream Responses API.

#### Scenario: WebSearch on the Codex bridge is rejected

- **GIVEN** a request whose `tools` contains `{"type": "web_search_20250305", "name": "web_search"}`
- **WHEN** a client calls `POST /v1/ccdex/messages`
- **THEN** the response is `400` with `error.type` `invalid_request_error`
- **AND** `error.message` contains `web_search_20250305`
- **AND** no upstream request is made
