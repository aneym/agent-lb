## Context

Normal Claude Code currently reaches its model through several repository-owned routing surfaces: the launcher default, the installer-managed Claude settings, the canonical routing table, and named Claude agent definitions. The driver and planner select Fable 5, while the frontend-designer policy row resolves to Opus 4.8. The driver and designer should move to Opus 5; the planner should remain Fable-first and use Opus 5 only after authoritative scoped exhaustion. GPT-5.6 Sol Explore, implementer, and verifier seats are separate Claude Code harness aliases and must remain unchanged.

The repository also contains Fable-specific account capability probes, cooldown keys, pricing, request analytics, and historical fixtures. Those values describe account state or recorded traffic; they are not current Claude Code route assignments and must not be mechanically renamed.

## Goals / Non-Goals

**Goals:**

- Converge the normal Claude Code driver, installed `cc` alias, and frontend-designer routes on canonical model `claude-opus-5` with high default effort.
- Keep the planner on Fable 5 while scoped capacity remains and fall back to Opus 5 only on full authoritative scoped exhaustion.
- Keep repository policy, installed Claude settings and agent definitions, launcher dry-run output, and routing verification consistent.
- Preserve every GPT/Sol seat's model, effort, and service-tier behavior.
- Preserve Fable quota/account telemetry and historical data semantics.
- Make installation and uninstall of any newly managed planner definition preservation-safe and idempotent.

**Non-Goals:**

- Renaming Fable capability probes, cooldown keys, pricing entries, analytics values, or historical fixtures.
- Changing account selection, quota settlement, or failover behavior outside the planner-only model resolution.
- Changing the CCDEX compatibility model or the GPT/Sol seat lineup.
- Adding a fallback to Opus 4.8 or a GPT model.
- Generalizing model selection beyond the fixed canonical seat policy.

## Decisions

### Use an explicit Opus 5 route identity

Repository surfaces for the driver and designer use `claude-opus-5` or Claude Code's supported `opus` selector. Verification must prove that the designer selector resolves to `claude-opus-5`.

This keeps launcher and installed-settings behavior deterministic while respecting Claude Code's supported agent-definition selector. Retaining an Opus 4.8 pin was rejected because it would not satisfy the migration; relying only on an unverified `opus` alias was rejected because static configuration alone would not prove the resolved route.

### Use a planner-only routing alias

The versioned planner definition uses Claude Code's supported full-model-ID field with `model: claude-planner` and pins `effort: high`. Agent-lb resolves that alias to `claude-fable-5` unless every otherwise-routable account is hard-excluded by a fresh Fable-scoped usage marker at the configured threshold with a future reset. Only that exact state permits `claude-opus-5`, and only if Opus itself has an eligible account.

Plain `model: claude-fable-5` was rejected because the proxy could not distinguish planner traffic from deliberate Fable requests. A generic Fable fallback was rejected because it would broaden the user's planner-only instruction. Claude Code frontmatter fallback fields and launcher-only preflight were rejected because no supported per-agent fallback field exists and child traffic does not pass through the launcher.

The proxy service exposes typed message and token-count resolution results. API-key model admission and the initial usage reservation consume those results before proxy execution, and the service receives the already-resolved concrete payload. The service remains the sole owner of scoped-quota interpretation, so API admission cannot duplicate stale quota logic or authorize the alias independently of its effective model.

### Treat the route inventory as an allowlisted migration

Implementation will update only repository-owned model-selection surfaces:

- the normal launcher default;
- the installer-managed direct Claude model;
- the installer-managed persistent Claude Code `effortLevel`;
- the omitted-model session-route fallback;
- the driver, planner-alias, and frontend-designer rows in the canonical routing policy;
- versioned Claude-native agent definitions and their installer/verifier assertions;
- focused tests and the two affected specifications.

Fable references outside that allowlist remain unchanged unless a focused test is asserting one of those route-selection surfaces. Opus 5 pricing is added alongside existing Fable pricing rather than replacing it. This prevents a broad search-and-replace from corrupting quota/account semantics.

### Install the planner definition with the same ownership discipline as the designer

The planner is a canonical seat but its route currently depends on a machine-local agent definition. The repository will version and install its definition, checkpoint any pre-existing file, record explicit ownership, preserve unrelated agent files, and remove it only when it remains owned and unmodified. This makes the Fable-primary planner route reproducible across machines.

Depending on an unversioned machine-local planner file was rejected because repository policy and installed behavior could drift.

### Preserve GPT/Sol route invariants explicitly

Explore, implementer, and verifier route rows and the CCDEX compatibility model will remain byte-for-byte equivalent in model, effort, and service-tier meaning. Focused verification will assert both the Opus 5 Claude-native routes and the unchanged GPT/Sol routes so the migration cannot silently broaden.

### Validate static convergence and exercised routing

Validation will cover installer and launcher tests, planner alias positive and negative integration scenarios, Python byte-compilation, routing verification against an isolated home, an installed/canonical `cc` dry run that reports Opus 5/high, and strict OpenSpec validation. Claude Code's official settings and subagent references document `effortLevel`, agent-level `effort`, high as a supported value, and full model IDs in agent frontmatter. After installation, live proof must separately cover direct Opus 5, frontend-designer Opus 5, planner Fable-primary behavior, and planner Opus 5 scoped-exhaustion fallback.

## Risks / Trade-offs

- [Claude Code's `opus` selector does not yet resolve to Opus 5 on an installed client] → Require current-head Claude Code and verify the resolved dispatch model before rollout.
- [A broad replacement changes Fable quota/account behavior] → Use the explicit route allowlist and add a regression assertion that telemetry constants remain Fable-specific.
- [The planner alias falls back on a generic failure] → Derive a dedicated boolean only from all-account fresh Fable-scoped hard exclusions and cover every negative case.
- [Installer convergence overwrites a user-authored planner definition] → Checkpoint before replacement, track explicit ownership, and preserve modified or never-managed files during uninstall.
- [A GPT/Sol seat changes accidentally while editing the lineup] → Assert the exact unchanged Explore, implementer, verifier, and CCDEX models in focused tests and verifier output.
- [Static files claim Opus 5 while installed state is stale] → Run the canonical installer, verifier, dry probe, and live selector-resolution check before publishing.

## Migration Plan

1. Update the canonical policy, launcher, versioned Claude-native definitions, installer, verifier, and focused tests.
2. Run focused launcher/installer tests, byte-compilation, Ruff, and strict OpenSpec validation.
3. Exercise a normal launcher dry run and isolated-home install/verification.
4. Install the validated policy on the active machine, then verify direct and frontend-designer Opus 5 plus both planner alias branches without changing GPT/Sol routes.
5. Restart the live service after validation because the omitted-model session-route fallback changes a server path.
6. After publish, fast-forward other authorized machines and rerun installer plus verification there.

Rollback uses the installer checkpoints to restore pre-existing agent definitions and reverts the route-bearing files to the previous validated commit. Fable telemetry requires no migration or rollback because it is unchanged.

## Open Questions

None. The driver and designer target `claude-opus-5`; the planner uses `claude-planner`, resolving Fable-first and Opus 5 only on scoped exhaustion.
