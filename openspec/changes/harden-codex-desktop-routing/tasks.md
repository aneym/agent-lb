## 1. Specification and guard

- [x] 1.1 Add the deployment-installation delta for durable Codex routing enforcement.
- [x] 1.2 Add a standalone, validated, text-preserving routing guard.
- [x] 1.3 Add no-op, direct-rewrite, missing-section, malformed-config, atomicity, and idempotency tests.

## 2. LaunchAgent lifecycle

- [x] 2.1 Add preview, install, provider discovery, owned-plist checks, load verification, and uninstall.
- [x] 2.2 Test LaunchAgent fields, provider selection, lifecycle convergence, and preview non-mutation.

## 3. Validation and rollout

- [x] 3.1 Run focused tests, ruff/compile checks, and strict OpenSpec validation.
- [x] 3.2 Install and exercise automatic live repair on Mac Studio; restart ChatGPT and prove one bundled Codex request in Agent LB logs.
- [ ] 3.3 Commit and push main, fast-forward MacBook without disturbing unrelated untracked work, then repeat installation and end-to-end proof.
- [ ] 3.4 Sync the stable specification and archive this verified change with rollout evidence.

### Mac Studio evidence

Verified 2026-07-17: the installed LaunchAgent watched the resolved symlink target `/Users/aneyman/.codex-shared/config.toml`, restored a simulated `openai` rewrite to `agent-lb` in 2.6 seconds, and preserved the symlink. After restarting ChatGPT (new PID 97529), the bundled Codex 0.145.0-alpha.18 returned exactly `LB_STUDIO_GUARD_OK_163116` with provider `agent-lb`. Agent LB recorded successful websocket `codex_exec` requests at 20:31:27Z and 20:31:30Z using `gpt-5.6-sol`.
