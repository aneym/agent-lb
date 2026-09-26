## Why

Claude Code's canonical driver and frontend-designer seat still select Fable 5 or Opus 4.8 even though Opus 5 is now available. The planner must remain Fable-first but needs a deterministic Opus 5 fallback when its dedicated Fable capacity is truly exhausted. The routing contract should add those behaviors while retaining the existing GPT/Sol execution seats and keeping Fable-specific account telemetry authoritative.

## What Changes

- Make Opus 5 with high effort the default for normal Claude Code sessions, including the installed `cc` alias.
- Route the frontend-designer seat to Opus 5.
- Route the planner through a dedicated `claude-planner` alias that selects Fable 5 while scoped capacity remains and Opus 5 only when every otherwise-routable account has a fresh, future-reset Fable-scoped exhaustion marker.
- Update installation and routing verification so repository policy, machine-installed policy, and dry-run launcher output agree on Opus 5.
- Preserve CCDEX and other GPT/Sol worker seats without changing their model, effort, or service-tier contract.
- Preserve Fable-specific quota probing, cooldowns, usage attribution, pricing, historical fixtures, and other non-routing account telemetry.
- Default omitted-model session-route claims to Opus 5 and add Opus 5 pricing so routed cost analytics remain populated.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `claude-harness-codex`: Change normal Claude Code and frontend-designer routing to Opus 5, add the Fable-primary planner alias, and preserve GPT/Sol compatibility routes.
- `deployment-installation`: Change installed Claude Code defaults and routing verification to converge on Opus 5.

## Impact

- Affects the canonical routing policy and Claude-native agent definitions under `config/coding-agents/`.
- Affects the normal Claude Code launcher default in `clients/claude-lb-launch`.
- Affects the omitted-model session-route fallback, planner alias resolution, and Anthropic Opus 5 pricing lookup.
- Affects installer, verifier, launcher, and policy tests that assert Claude-native model selection.
- Affects the `claude-harness-codex` and `deployment-installation` requirements.
- Does not change Fable capability probing, historical request data, existing Fable pricing, or GPT/Sol compatibility routing.
