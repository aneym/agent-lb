# claude-harness-codex Specification

## Purpose

Define how agent-lb runs the real Claude Code harness with a locked GPT-5.6 Sol execution profile through a dedicated OpenAI Responses compatibility bridge.

## Requirements

### Requirement: Claude Code harness execution
The system SHALL provide a `ccdex` command that executes the installed Claude Code harness while routing inference through agent-lb's dedicated Codex compatibility path.

#### Scenario: Interactive launch
- **WHEN** an operator runs `ccdex`
- **THEN** the real Claude Code interactive harness starts with the compatibility route enabled

#### Scenario: Headless launch
- **WHEN** an operator runs `ccdex -p <prompt>`
- **THEN** Claude Code completes the headless turn through the compatibility route

### Requirement: Locked Sol execution profile
Every compatibility inference request MUST use canonical model `gpt-5.6-sol`, reasoning effort `high`, and requested service tier `priority`, regardless of weaker Claude model, effort, speed, or environment defaults.

#### Scenario: Conflicting client controls
- **WHEN** Claude Code or its environment supplies a different model, reasoning level, or service tier
- **THEN** the server sends `gpt-5.6-sol`, `high`, and `priority` to the Responses route

### Requirement: Fail-closed launch
The `ccdex` launcher MUST exit nonzero when agent-lb or its compatibility capability is unavailable and MUST NOT fall back to plain Claude or an Anthropic account.

#### Scenario: Agent-lb unavailable
- **WHEN** preflight cannot reach a compatible agent-lb instance
- **THEN** `ccdex` reports the failure, exits nonzero, and does not execute an unproxied inference request

### Requirement: Messages and Responses protocol fidelity
The bridge MUST preserve ordered system and message text, supported images, tools, tool choice, parallel tool controls, tool calls, tool results, usage, stop reasons, streaming lifecycle, and Anthropic-native error envelopes across the Messages and Responses protocols.

#### Scenario: Tool-use round trip
- **WHEN** GPT-5.6 Sol streams a function call and Claude Code returns its tool result
- **THEN** Claude Code receives one valid `tool_use` block and the following Responses request contains the matching `function_call_output`

#### Scenario: Text streaming
- **WHEN** Responses emits streamed output text
- **THEN** the bridge emits an ordered Messages sequence ending with exactly one `message_delta` and one `message_stop`

### Requirement: No hidden reasoning disclosure
The bridge MUST NOT expose private chain-of-thought and SHALL replay only validated opaque encrypted reasoning state needed for multi-turn continuity.

#### Scenario: Reasoning response
- **WHEN** Responses returns reasoning metadata or encrypted content
- **THEN** Claude Code receives no raw hidden reasoning text and only validated opaque state may be replayed

### Requirement: Safe token counting behavior
Compatibility token-count requests MUST NOT select or call an Anthropic account.

#### Scenario: Canonical token count unavailable
- **WHEN** Claude Code requests `/v1/messages/count_tokens` through `ccdex` before a canonical GPT counter exists
- **THEN** agent-lb returns a conservative local estimate without upstream Anthropic traffic
