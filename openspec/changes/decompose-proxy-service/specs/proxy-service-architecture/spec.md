## ADDED Requirements

### Requirement: Proxy service decomposition preserves the public facade
The proxy service decomposition SHALL keep `app.modules.proxy.service` as the
stable public import surface for proxy callers. `ProxyService` and exported
core helper names that callers import from `app.modules.proxy.service` MUST
continue to resolve from that module after domain implementations move into
`app.modules.proxy._service`.

Compatibility modules `app.modules.proxy._support` and
`app.modules.proxy._warmup` SHALL remain re-export shims for moved names during
the incremental migration. These shims MUST NOT own new proxy behavior.

#### Scenario: Existing imports continue to resolve
- **GIVEN** downstream code imports `ProxyService` or exported core helper names
  from `app.modules.proxy.service`
- **WHEN** the proxy package is imported after decomposition
- **THEN** those imports resolve without requiring callers to import from
  `app.modules.proxy._service`
- **AND** legacy `_support` and `_warmup` imports still resolve through
  compatibility shims

### Requirement: Proxy implementation domains stay inside a private package
The decomposed proxy implementation SHALL place domain implementations under
`app.modules.proxy._service`. The private package MUST include owned modules or
packages for support, warmup, HTTP bridge, websocket, streaming, compact
responses, file operations, Codex control, transcription, request logging,
API-key usage, rate-limit payloads, observability, and response-create payload
handling.

Cross-domain imports inside `app.modules.proxy._service` MUST stay within the
allowed dependency directions enforced by the architecture check so extracted
domains do not rebuild the original monolithic dependency graph.

#### Scenario: Required private domains are present
- **WHEN** the architecture check inspects `app/modules/proxy/_service`
- **THEN** all required private domain modules and packages are present
- **AND** disallowed cross-domain imports fail the check

### Requirement: Architecture ratchets block proxy monolith regression
The project SHALL provide a CI-visible architecture check that is callable with
`make architecture-check` and included in `make lint`. The check MUST fail when
the proxy facade or extracted bridge/streaming mixins exceed their configured
line-count thresholds, when required private domains disappear, when
compatibility shims gain implementation logic, when required facade exports
disappear, or when extracted domains introduce disallowed cross-domain imports.

#### Scenario: Lint catches architecture regression
- **WHEN** `make lint` or `make architecture-check` is run after a proxy edit
- **THEN** the proxy architecture check enforces facade-size, domain-presence,
  shim-only, facade-export, and cross-domain import ratchets
- **AND** a regression in any ratchet fails the command before review
