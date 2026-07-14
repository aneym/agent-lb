## Why

Recent CCDEX sessions ran a GPT root but allowed Claude-model `Agent` and inline `Workflow` children, so the effective executor depended on each inner model literal rather than the selected development mode. Normal Claude Code also lacks a registered direct CCDEX worker, forcing Fable to choose between doing implementation itself and using inconsistent delegation paths.

## What Changes

- Define two fail-closed launcher profiles: normal `cc` defaults to Fable, while `ccdex` forces the configured GPT compatibility model.
- Reject non-GPT model requests inside CCDEX instead of passing them to the ordinary Anthropic route.
- Install and register the durable CCDEX worker MCP so normal Fable-led Claude Code can send implementation work directly to GPT.
- Add deterministic launcher, hook/policy, installation, and live-session validation for parent and child model identity.
- **BREAKING**: CCDEX no longer permits Claude-model subagents or workflow phases; callers must use the GPT main loop or CCDEX worker transport.

This supersedes the selective Claude-model passthrough promised by the still-active `fix-ccdex-effort-and-passthrough` change. Per-task effort propagation remains valid; only passthrough is retired.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `claude-harness-codex`: make normal-Claude and CCDEX model profiles explicit and reject Claude-model child inference in CCDEX.
- `deployment-installation`: install and register the CCDEX worker MCP as part of Claude Code client wiring.

## Impact

The launcher, CCDEX worker client installation, focused tests, and machine-local Claude/Codex routing adapters are affected. The Responses bridge model remains controlled by the existing CCDEX compatibility contract. The change does not add a model provider or silently fall back between Claude and GPT.
