# anthropic-messages-compat Specification

## Purpose

Define how agent-lb accepts Anthropic Messages API requests and forwards them upstream, including tool definitions Claude Code sends.

## Requirements

### Requirement: Versioned Anthropic server tools pass through on the Messages route

The `/v1/messages` route SHALL accept tool definitions that carry a `type` other than `custom` (Anthropic-defined server tools such as `web_search_20250305`) without requiring an `input_schema`. Such tools MUST be forwarded to the upstream Anthropic request unchanged, including every provider field on the definition. Custom tools (no `type`, or `type: "custom"`) MUST still require an `input_schema` and MUST be rejected as an invalid request when it is missing.

#### Scenario: Claude Code WebSearch reaches upstream

- **GIVEN** a `/v1/messages` request whose `tools` contains `{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}`
- **WHEN** the proxy forwards the request upstream
- **THEN** the request is accepted
- **AND** the upstream body contains that tool definition unchanged

#### Scenario: Custom tool without schema is rejected

- **GIVEN** a `/v1/messages` request whose `tools` contains a definition with only a `name`
- **WHEN** the proxy validates the request
- **THEN** the request is rejected as invalid and nothing is sent upstream

### Requirement: Codex bridge fails closed on server tools

The Codex bridge route (`/v1/ccdex/messages`) SHALL reject any request whose `tools` include an Anthropic server tool with HTTP 400 and an Anthropic `invalid_request_error` envelope naming the unsupported tool type. It MUST NOT translate the request with the server tool dropped, and MUST NOT send any request upstream.

#### Scenario: WebSearch on the Codex bridge

- **GIVEN** a `/v1/ccdex/messages` request whose `tools` contains `{"type": "web_search_20250305", "name": "web_search"}`
- **WHEN** the proxy handles the request
- **THEN** the response is HTTP 400 with `error.type` `invalid_request_error` and a message naming `web_search_20250305`
- **AND** no upstream Responses request is made
