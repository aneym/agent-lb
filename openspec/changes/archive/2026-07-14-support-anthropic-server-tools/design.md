## Context

The `/v1/messages` route parses request bodies into `AnthropicMessageRequest` before proxy code runs. Its current `tools` field accepts only `AnthropicToolDefinition`, whose required `name` and `input_schema` fields model client-executed custom tools. Anthropic server tools use a different contract: a required versioned `type` plus a fixed `name`, with tool-specific optional fields and no `input_schema`. Claude Code therefore receives a local HTTP 400 `Field required` response for valid WebSearch requests. Once validation succeeds, the proxy already serializes the Pydantic model with `extra="allow"` and forwards the body and stream without tool-specific rewriting.

## Goals / Non-Goals

**Goals:**

- Represent custom tools and Anthropic-defined tools as distinct request variants.
- Preserve every accepted tool field byte-for-JSON-value when forwarding upstream.
- Keep the change small and compatible with existing Claude Code payloads.
- Prove the externally failing `/v1/messages` path and a live WebSearch request.

**Non-Goals:**

- Reimplement Anthropic web search, add search-specific beta headers, or translate search results.
- Enumerate every current and future Anthropic-defined tool type in agent-lb.
- Change the optional ccdex Messages-to-Responses bridge, which is a separate capability.

## Decisions

1. Model `tools` as a union of the existing custom-tool shape and a generic Anthropic-defined tool shape requiring `type` and `name`.
   - This preserves useful validation for custom tools while accepting current and future server/client-defined Anthropic tool variants.
   - A fully untyped `list[JsonObject]` was rejected because it would discard existing boundary validation.
   - Enumerating only web-search versions was rejected because it would repeat the same compatibility failure for web fetch, code execution, or later tool revisions.

2. Do not rewrite tool versions or add a web-search beta header.
   - Official SDK types require `name: "web_search"` with `type: "web_search_20250305"` or `"web_search_20260209"`; both are ordinary Messages API tool definitions.
   - The proxy should preserve the caller-selected version and existing inbound beta header. Current web-search server tools do not require a feature-specific beta header.

3. Keep response parsing unchanged.
   - The Anthropic stream is forwarded raw. Local parsing is used only for usage extraction, so unrecognized server-tool result events do not corrupt the downstream stream.

## Risks / Trade-offs

- [Risk] A malformed custom tool containing a `type` could match the generic variant. → Mitigation: discriminate by the presence of `type`; upstream remains authoritative for Anthropic-defined tool-specific validation, while ordinary custom tools retain local validation.
- [Risk] Future code may assume every tool has `input_schema`. → Mitigation: add typed regression tests and keep downstream consumers operating on serialized mappings rather than custom-tool attributes.
- [Risk] Unit tests prove acceptance but not subscription OAuth availability. → Mitigation: after restart, exercise one real WebSearch call through `http://127.0.0.1:2455` and confirm server-tool events or cited search output.

## Migration Plan

1. Add the request-model union and regression tests.
2. Run focused unit/integration tests, Ruff, import/compile checks, and strict OpenSpec validation.
3. Restart `com.aneyman.agent-lb` with `launchctl kickstart -k`.
4. Exercise the live `/v1/messages` WebSearch path.
5. Roll back by reverting the commit and restarting the service if the live probe regresses custom-tool traffic.

## Open Questions

- None. The live request and official generated SDK types establish the required wire contract.
