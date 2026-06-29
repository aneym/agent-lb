## ADDED Requirements

### Requirement: Anthropic fast-mode quota isolation
Anthropic Messages requests with `speed: "fast"` MUST use a dedicated `anthropic_fast` quota family for account prefiltering, upstream 429 cooldown persistence, cooldown clearing, and retry metadata. Anthropic Messages requests without `speed: "fast"` MUST continue to use the underlying model/effort quota family, even when they carry a fast-mode beta header.

#### Scenario: Fast cooldown does not block standard fallback
- **GIVEN** a Claude session sends a top-thinking request with `speed: "fast"`
- **AND** Anthropic returns a fast-mode rate-limit response for the selected account
- **WHEN** the same Claude session retries without `speed: "fast"`
- **THEN** account selection uses `anthropic_top_thinking`
- **AND** it is not blocked by the recorded `anthropic_fast` cooldown

#### Scenario: Fast reset headers are honored
- **GIVEN** Anthropic returns a rate-limit response with fast-mode reset headers
- **WHEN** agent-lb records the quota cooldown
- **THEN** retry metadata uses the fast-mode reset time

### Requirement: Anthropic fast-mode upstream headers
Anthropic OAuth-backed Messages requests MUST carry the Anthropic OAuth beta header upstream. Anthropic Messages requests with `speed: "fast"` MUST also carry the fast-mode beta header upstream, even when the client omits it.

#### Scenario: Fast request gets required betas
- **GIVEN** an Anthropic Messages request has `speed: "fast"`
- **AND** the client omits `anthropic-beta`
- **WHEN** agent-lb forwards the request upstream
- **THEN** the upstream request includes `oauth-2025-04-20`
- **AND** it includes `fast-mode-2026-02-01`

### Requirement: Anthropic fast-mode preserves Claude session affinity
Claude session stickiness for Anthropic Messages requests MUST be keyed by the underlying model/effort quota family rather than by the transient request speed.

#### Scenario: Fast and standard turns share affinity
- **GIVEN** a Claude session alternates between fast and standard top-thinking requests
- **WHEN** agent-lb derives the session sticky key
- **THEN** both requests use the same `anthropic_top_thinking` session-affinity key
- **AND** fast-mode quota cooldowns remain isolated under `anthropic_fast`
