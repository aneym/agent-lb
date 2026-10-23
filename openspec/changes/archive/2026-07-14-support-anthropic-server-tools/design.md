## Context
Anthropic executes server tools. For example, `{"type":"web_search_20250305","name":"web_search","max_uses":5}` has no client schema.

## Decisions
Use a distinct server-tool model with a versioned type, required name, and preserved extra fields. Custom tools retain required object schemas. Reject server tools at the bridge route before translation or dispatch, including tools supplied with a schema. This avoids silently omitting tools or treating them as client functions.

## Constraints
Versioned identifiers use lowercase letters, digits, underscores and an eight-digit suffix. Unknown unversioned tool types remain invalid. No provider-specific field allowlist is maintained.

## Failure modes
Bridge requests containing any server tool return HTTP 400 with an Anthropic invalid_request_error envelope. Mixed requests also fail before upstream dispatch.

## Validation
Run existing Anthropic and bridge tests, lint and strict OpenSpec validation. External acceptance remains pending because the supplied harness is missing on this seat.
