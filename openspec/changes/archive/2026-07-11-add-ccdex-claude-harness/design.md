## Context

Claude Code 2.1.207 accepts arbitrary model identifiers behind a custom gateway and sends the Anthropic Messages protocol. Agent-lb's `/v1/messages` path currently forwards only to Anthropic-compatible providers, while its Responses path already supplies pooled OpenAI account selection, affinity, failover, accounting, and streaming. A live direct Responses request proved `gpt-5.6-sol` with high effort works; requested priority currently may settle as default and must remain observable as requested versus actual.

CLIProxyAPI demonstrates the required protocol seam under its MIT-licensed `internal/translator/codex/claude` package. We will implement the same protocol concepts in the repo's typed Python architecture, without importing a Go runtime or running a second daemon.

## Goals / Non-Goals

**Goals:**

- Run the genuine Claude Code harness with GPT-5.6 Sol through agent-lb.
- Preserve text, tools, images, multi-turn tool results, streaming order, errors, cancellation, usage, and session affinity.
- Lock the route to high reasoning and requested priority service, while reporting actual service tier honestly.
- Fail closed and keep ordinary Claude/Anthropic traffic unchanged.

**Non-Goals:**

- Claim Anthropic support for non-Claude models.
- Replace Claude Code's harness, shell out to Codex CLI, or add another resident proxy.
- Expose hidden chain-of-thought.
- Guarantee that an upstream priority request is fulfilled as priority when upstream reports default.

## Decisions

### Dedicated compatibility endpoint selected by the launcher

`ccdex` reuses the existing Claude launcher MITM so Claude Code still sees the first-party Anthropic host where needed, but inference is sent to a dedicated local agent-lb Messages compatibility endpoint. This is safer than allowing a spoofable header to alter ordinary `/v1/messages` routing and avoids coupling provider selection to arbitrary model prefixes.

### Compose over ProxyService

A focused bridge converts `AnthropicMessageRequest` into `ResponsesRequest`, then calls `ProxyService.stream_responses` with OpenAI cache/session affinity enabled. It does not enter `AnthropicProxyService`, preserving provider ownership and existing settlement behavior.

### Stateful typed SSE translation

The bridge owns a per-request state machine that emits Anthropic Messages SSE in protocol order. It maps Responses text and function-call events, terminal usage and errors, buffers incomplete tool-call metadata when necessary, and emits exactly one terminal sequence. Non-streaming Messages responses are collected from the same normalized event stream.

### Fixed execution profile

The launcher and server independently enforce `gpt-5.6-sol`, `reasoning.effort=high`, and `service_tier=priority`. Server enforcement prevents client flags or Claude thinking settings from weakening the contract. Requested and actual tiers remain separate in logs.

### Conservative reasoning continuity

The Responses request includes encrypted reasoning output, but the bridge never exposes raw reasoning. Opaque encrypted reasoning may be carried through a bridge-owned thinking signature only after validation; unsupported foreign signatures are discarded rather than replayed.

### Conservative local token counting

Until a canonical GPT token-count endpoint exists, the compatibility count route returns a conservative local estimate and never selects an Anthropic account. This keeps Claude Code's context-management preflight working without claiming exact tokenizer parity.

## Risks / Trade-offs

- **Claude Code protocol drift** -> Keep the bridge isolated, retain unknown request fields at the parsing edge, and maintain real-harness integration coverage.
- **Tool streaming lifecycle mismatch** -> Use a state machine with fragmented-argument, parallel-call, error, cancellation, and terminal-event tests.
- **Reasoning continuity loss** -> Preserve only authenticated opaque encrypted state and test tool-turn replay; never synthesize or reveal chain-of-thought.
- **Priority not honored upstream** -> Request priority, record actual tier separately, and report degradation honestly.
- **Remote Control sensitivity to custom gateways** -> Reuse the established launcher MITM rather than a plain base-URL wrapper.
- **Credential leakage** -> Strip Claude authorization headers before entering the OpenAI route and let agent-lb inject the selected OpenAI account credential.

## Migration Plan

Ship the server bridge and bootstrap model first, validate it through the dedicated endpoint, then install/expose `ccdex`. Restart the local launchd service only after validation and exercise text plus tool-use turns. Rollback removes the command and route; ordinary `/v1/messages` remains unaffected throughout.

## Open Questions

- Whether upstream will honor priority tier for the local subscription pool is runtime-dependent and must be established from actual response metadata, not configuration alone.
