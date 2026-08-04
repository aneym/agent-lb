## ADDED Requirements

### Requirement: Launcher proxy export excludes loopback destinations

Whenever `claude-lb-launch` exports `HTTPS_PROXY`/`https_proxy` for the per-session intercepting proxy, it MUST also ensure `NO_PROXY` and `no_proxy` contain `127.0.0.1` and `localhost`, preserving any pre-existing exclusion entries and adding no duplicates. Descendant processes that honor proxy environment variables MUST therefore connect directly to loopback-hosted services (e.g. `http://127.0.0.1:2455`).

#### Scenario: Descendant tool calls a loopback API

- **GIVEN** a shell or tool spawned from a launcher-managed Claude session with `HTTPS_PROXY` set
- **WHEN** it POSTs to `http://127.0.0.1:2455/v1/chat/completions` with a proxy-honoring HTTP client
- **THEN** the request connects directly to 127.0.0.1:2455 and is not routed through the MITM proxy

#### Scenario: Existing NO_PROXY entries are preserved

- **GIVEN** the process environment already has `NO_PROXY=api.anthropic.com`
- **WHEN** the launcher exports the proxy environment
- **THEN** `NO_PROXY` becomes `api.anthropic.com,127.0.0.1,localhost`
