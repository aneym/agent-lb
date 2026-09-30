## ADDED Requirements

### Requirement: Tagged main threads stand in on eligible pool failures
The Messages handler SHALL use the existing GPT bridge for Opus or Sonnet requests tagged with a live-policy-mapped intent, carrying no x-claude-code-agent-id, when Anthropic selection, non-stream collection, or streaming before the first chunk fails with HTTP 429, HTTP 500 or greater, or a no_available_ error code. It SHALL release the Claude reservation, resolve the live policy.stand_in alias, and identify the replacement and intended model in x-agent-lb-standing-in. Missing policy.stand_in SHALL default both intents to sol-latest-high. Unmapped intents and subagents SHALL retain existing errors and streaming behavior. Claude Code system blocks SHALL not be added, removed, or reordered.

#### Scenario: Empty pool and pre-first-chunk exhaustion
- **GIVEN** a tagged orchestrator main thread and an unavailable Anthropic pool
- **WHEN** selection fails or every account returns 429 before the first stream chunk
- **THEN** the turn is answered through the resolved Sol bridge with its configured effort
- **AND** the response identifies the stand-in and intended model

#### Scenario: Subagents keep Claude errors
- **GIVEN** a request carrying x-claude-code-agent-id or no mapped intent
- **WHEN** Anthropic fails
- **THEN** the existing Anthropic error behavior is preserved without prefetching the stream

### Requirement: Session shims carry lane intent
The per-session shim SHALL read AGENT_LB_INTENT and AGENT_LB_LANE once at startup and add their non-empty values as x-agent-lb-intent and x-agent-lb-lane alongside the session header. The launcher SHALL remove both variables from the Claude child's environment after the shim captures them, so nested launches do not inherit the intent. The shared desktop shim SHALL add neither tag.

#### Scenario: A lane-tab launcher tags its requests
- **GIVEN** non-empty lane-tab intent and lane environment variables
- **WHEN** the session shim forwards a request to agent-lb
- **THEN** both tags accompany its session header
