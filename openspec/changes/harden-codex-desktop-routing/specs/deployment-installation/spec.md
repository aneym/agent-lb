## ADDED Requirements

### Requirement: macOS Codex routing guard preserves local Agent LB routing

The project SHALL provide a macOS Codex routing guard and installer that keep a host-configurable Codex model provider routed to the local Agent LB endpoint. The guard MUST validate the complete TOML document before mutation, MUST preserve unrelated configuration and provider identity, and MUST repair only top-level `model_provider` plus `base_url`, `wire_api`, `supports_websockets`, and `requires_openai_auth` in the selected provider table. A repair MUST require a healthy loopback Agent LB, MUST use a mode-preserving atomic replacement, and MUST be idempotent. Malformed configuration MUST fail without clobbering the source.

The installer MUST install a dedicated per-user LaunchAgent with `RunAtLoad`, `WatchPaths` for the Codex config, and a modest `StartInterval` fallback. Preview MUST be non-mutating, uninstall MUST remove only an owned LaunchAgent, and recurring failures MUST NOT produce desktop notifications or unbounded duplicate logs.

#### Scenario: ChatGPT changes the active provider to direct OpenAI

- **GIVEN** a valid Codex config selects `openai`
- **AND** the configured host provider is `agent-lb` or `codex-lb`
- **AND** local Agent LB health succeeds
- **WHEN** the routing guard runs
- **THEN** the active provider is restored to the configured host provider
- **AND** its provider table targets `http://127.0.0.1:2455/backend-api/codex` with Responses websocket and OpenAI-auth support enabled
- **AND** unrelated configuration remains unchanged

#### Scenario: Provider table is missing

- **GIVEN** a valid Codex config does not contain the configured host provider table
- **AND** local Agent LB health succeeds
- **WHEN** the routing guard runs
- **THEN** exactly one canonical provider table is appended
- **AND** repeated runs make no further byte changes

#### Scenario: Config is malformed

- **GIVEN** the Codex config is invalid TOML or contains duplicate managed tables
- **WHEN** the routing guard runs
- **THEN** it exits with a diagnostic
- **AND** the original file bytes are unchanged

#### Scenario: ChatGPT writes after login

- **GIVEN** the dedicated LaunchAgent is installed
- **WHEN** ChatGPT replaces the watched Codex config after an app update
- **THEN** launchd invokes the guard from the watched-path event or interval fallback
- **AND** routing eventually converges without an immutable-file restriction or desktop-notification loop
