## MODIFIED Requirements

### Requirement: Locked Sol execution profile
Every compatibility inference request MUST use canonical model `gpt-5.6-sol` and requested service tier `priority`, regardless of weaker Claude model, speed, or environment defaults. Reasoning effort SHALL honor a supported per-request value (`low`, `medium`, `high`, `xhigh`) supplied by the Claude Code harness via `output_config.effort` and MUST default to `high` when the value is missing or unsupported.

#### Scenario: Conflicting client controls
- **WHEN** Claude Code or its environment supplies a different model or service tier
- **THEN** the server sends `gpt-5.6-sol` and `priority` to the Responses route

#### Scenario: Per-task reasoning effort
- **WHEN** a compatibility request carries `output_config.effort` of `low`, `medium`, `high`, or `xhigh`
- **THEN** the translated Responses request and its accounting use that effort

#### Scenario: Unsupported effort value
- **WHEN** a compatibility request carries a missing or unsupported effort value
- **THEN** the translated Responses request uses `high`

## ADDED Requirements

### Requirement: Selective Claude-model passthrough
In ccdex mode, the launcher MUST route only requests whose body names `gpt-5.6-sol` to the compatibility route; requests naming any other model MUST pass through on their original path, and token-count requests MUST always use the local compatibility counter. Outside ccdex mode the launcher MUST NOT rewrite any path.

#### Scenario: Sol worker request
- **WHEN** ccdex forwards a `/v1/messages` body whose model is `gpt-5.6-sol`
- **THEN** the request is rewritten to `/v1/ccdex/messages`

#### Scenario: Claude subagent passthrough
- **WHEN** ccdex forwards a `/v1/messages` body naming a Claude model (for example a Fable planning or Opus frontend subagent)
- **THEN** the path is not rewritten and the request reaches the ordinary Anthropic route

#### Scenario: Regular cc unchanged
- **WHEN** the launcher runs without ccdex mode
- **THEN** no path is rewritten, even for a body naming `gpt-5.6-sol`
