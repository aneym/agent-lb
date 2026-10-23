# Anthropic Messages Compatibility

## Purpose
Preserve tool requests on Anthropic and reject unsupported bridge tools.

## Requirements

### Requirement: Server tools pass through Messages
The `/v1/messages` route SHALL accept server tools with a lowercase identifier and an eight-digit version suffix, require a name, and preserve supplied tool fields without requiring `input_schema`.

#### Scenario: Versioned WebSearch
- **WHEN** a request supplies `web_search_20250305` with a name and provider options
- **THEN** the tool SHALL reach Anthropic with its supplied fields intact

### Requirement: Custom tools retain validation
Tools with an omitted type or `custom` type SHALL require an object `input_schema`. Unknown unversioned types SHALL be rejected.

#### Scenario: Missing custom schema
- **WHEN** a custom tool omits `input_schema`
- **THEN** validation SHALL reject the request

### Requirement: Bridge rejects server tools
The `/v1/ccdex/messages` route SHALL reject server tools before translation or upstream dispatch with HTTP 400 and an Anthropic `invalid_request_error` identifying the unsupported tool.

#### Scenario: Unsupported bridge tool
- **WHEN** a streaming or non-streaming bridge request contains any server tool, including mixed lists and server tools with schemas
- **THEN** the bridge SHALL reject the request without upstream dispatch

#### Scenario: Custom bridge tool
- **WHEN** a bridge request contains only valid custom tools
- **THEN** existing function-tool translation SHALL remain available
