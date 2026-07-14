## Context

This shipped change originally combined two behaviors: per-request effort propagation and selective passthrough of Claude-model child requests from CCDEX. The effort behavior remains part of the compatibility bridge. The later `enforce-coding-agent-modes` change supersedes selective passthrough after session evidence showed that Agent and Workflow model literals made CCDEX routing unpredictable.

## Decisions

### Preserve per-request effort propagation

Supported `output_config.effort` values continue to reach the Responses request and accounting. Missing or unsupported values continue to default to high.

### Treat selective passthrough as historical behavior

Selective passthrough accurately described the first shipped implementation, so this change retains it as historical design evidence. The superseding change removes that requirement and replaces it with fail-closed GPT-only inference inside CCDEX.

## Migration

Fable planning runs in normal Claude Code. Normal Claude Code sends implementation to the CCDEX worker transport. CCDEX itself uses only its GPT main loop or GPT/Codex worker fan-out.
