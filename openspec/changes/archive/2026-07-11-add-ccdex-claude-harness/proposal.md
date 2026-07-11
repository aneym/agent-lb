## Why

GPT-5.6 Sol currently runs through Codex's harness even when users prefer Claude Code's orchestration, tools, subagents, and context management. Agent-lb already owns the pooled ChatGPT/Codex route, so it should expose a truthful `ccdex` command that runs Claude Code's harness while translating its Anthropic Messages traffic to fixed GPT-5.6 Sol Responses traffic.

## What Changes

- Add an explicit `ccdex` launcher that executes the installed Claude Code binary and fails closed when the local agent-lb compatibility endpoint is unavailable.
- Add an authenticated/local compatibility route that translates Claude Messages requests and SSE/JSON responses to and from the OpenAI Responses protocol.
- Lock `ccdex` inference to `gpt-5.6-sol`, high reasoning effort, and priority (fast) service tier while preserving Claude Code tools, subagents, session continuity, and error semantics.
- Route translated traffic only through eligible OpenAI accounts, with existing agent-lb selection, affinity, settlement, limits, and observability.
- Preserve ordinary `/v1/messages` and `cc` behavior as the Anthropic-provider path.
- Advertise and route the canonical GPT-5.6 Sol model without silently substituting another model.

## Capabilities

### New Capabilities

- `claude-harness-codex`: Claude Code harness launch, protocol translation, locked execution profile, and fail-closed behavior for `ccdex`.

### Modified Capabilities

- `account-routing`: Translated Claude-harness traffic selects only eligible OpenAI accounts and retains session/cache affinity and failover behavior.
- `model-catalog-compat`: GPT-5.6 Sol is discoverable and routable as its canonical model identifier.
- `responses-api-compat`: The Responses surface accepts the translated Claude Code workload with fixed reasoning and service-tier controls and maps results back to Messages semantics.
- `runtime-portability`: Installed environments expose a portable `ccdex` console command that invokes the real Claude Code binary.

## Impact

The change affects the client launchers, FastAPI proxy routes, typed Anthropic/Responses models, protocol translation, OpenAI account routing and request logging, model bootstrap behavior, packaging console scripts, OpenSpec contracts, and focused unit/integration/end-to-end tests. It introduces no frontend behavior and does not change ordinary Claude Code routing.
