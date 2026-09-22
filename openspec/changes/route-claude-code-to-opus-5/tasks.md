## 1. Lock the Routing Contract in Tests

- [x] 1.1 Update focused launcher and client-installer tests first to require `claude-opus-5` with high effort for normal Claude Code and the installed `cc` alias while retaining the existing CCDEX compatibility model with high effort.
- [x] 1.2 Add installer tests for first install, checkpointing, preview, idempotence, managed uninstall, customized-file preservation, and never-managed preservation of the canonical planner definition.
- [x] 1.3 Add routing-policy/verifier assertions for the Opus 5 driver and frontend-designer, Fable-primary planner alias, and unchanged Explore, implementer, verifier, and CCDEX GPT/Sol routes.
- [x] 1.4 Add a regression assertion that Fable probe models, quota/cooldown keys, pricing, usage attribution, and historical fixtures are not migrated as route selections.
- [x] 1.5 Add focused tests for the omitted-model session-route fallback and canonical/versioned Opus 5 pricing while retaining Fable pricing.
- [x] 1.6 Add planner alias tests for available Fable capacity, full scoped exhaustion, partial exhaustion, soft weekly threshold, stale markers, generic request cooldown, total Anthropic exhaustion, and count-tokens parity.
- [x] 1.7 Add message and count-tokens API-key regressions for allowed effective planner models, disallowed effective models, and message reservation/settlement identity.

## 2. Converge Repository Routing

- [x] 2.1 Change the normal Claude launcher default and installer-managed direct Claude model to `claude-opus-5` with high launcher and persistent `effortLevel` defaults without changing CCDEX model, effort, or service-tier controls.
- [x] 2.2 Update `config/coding-agents/ROUTING.md` so the driver and frontend-designer resolve to Opus 5, the planner records its Fable-primary alias contract, and the GPT/Sol seat rows remain unchanged.
- [x] 2.3 Add or update versioned planner and frontend-designer definitions so the designer resolves to Opus 5 and the planner selects `claude-planner` with high effort.
- [x] 2.4 Extend the policy installer to converge the planner definition with checkpointing and explicit ownership semantics matching the frontend-designer safety contract.
- [x] 2.5 Extend `verify-routing` to reject Fable 5 or Opus 4.8 on any migrated Claude-native route and to confirm the unchanged GPT/Sol lineup.
- [x] 2.6 Change the omitted-model session-route fallback to Opus 5 and add Opus 5 pricing/alias support without changing existing Fable telemetry or pricing.
- [x] 2.7 Resolve `claude-planner` before message and token-count account selection, payload forwarding, logs, pricing, and settlement, falling back only on full authoritative Fable-scoped exhaustion.
- [x] 2.8 Resolve planner aliases through typed service results before API-key admission and initial reservation so the alias cannot bypass effective-model policy.
- [x] 2.9 Keep historical Fable sessions labeled as Driver while also labeling Opus 5 sessions as Driver in session analytics.

## 3. Validate the Change

- [ ] 3.1 Byte-compile the launcher, installer, and verifier; run `ruff check app clients config/coding-agents tests`; and run the focused launcher/installer/routing test set.
- [x] 3.2 Install into an isolated temporary home, run the routing verifier, and prove preview, repeated install, checkpoint, and uninstall behavior without touching real user state.
- [x] 3.3 Run the installed/canonical `cc` alias with `CLAUDE_LB_DRY_RUN=1` and no model/effort, confirm `claude-opus-5` with high effort, and prove the CCDEX dry run remains on its canonical GPT model with high effort.
- [x] 3.4 Run strict OpenSpec validation with `npx --yes @fission-ai/openspec@latest validate --specs` and resolve every failure.

## 4. Install and Publish the Validated Policy

- [ ] 4.1 Run the canonical installer on the active machine, verify direct Claude Code and frontend-designer resolution to `claude-opus-5`, verify both planner alias branches, and separately report any authentication or capacity failure.
- [ ] 4.2 Restart `com.aneyman.agent-lb` after the server-path validation gate and verify the omitted-model session-route fallback live.
- [ ] 4.3 Sync the delta requirements into the main specs, verify the completed OpenSpec change, and archive it only after runtime evidence passes.
- [ ] 4.4 Commit the validated change on `main`, push `origin/main`, then fast-forward each authorized peer checkout and rerun installer plus routing verification so all Claude Code installations converge.
