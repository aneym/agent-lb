## Why

The Claude client installer links the canonical policy and executables but leaves the global Claude/Codex adapters, default model, and CCDEX guard hook unmanaged. A clean second machine can therefore pass installation while failing the repository's routing verifier or retaining contradictory legacy model instructions.

## What Changes

- Extend client installation to converge the routing-specific portions of global Claude and Codex configuration.
- Preserve unrelated Markdown sections, JSON keys, hooks, permissions, and machine-specific settings.
- Version and install the CCDEX GPT-only guard hook from the repository.
- Make preview and repeated installation deterministic, and make verification fail clearly when a required file is absent.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: Require policy installation to converge global routing adapters, the Fable default, and the CCDEX guard without overwriting unrelated configuration.

## Impact

Affected surfaces are `scripts/install-claude-clients.sh`, a policy configuration helper and hook under `config/coding-agents/`, the deterministic routing verifier, installer tests, and the deployment-installation specification. No server runtime or database behavior changes.
