## ADDED Requirements

### Requirement: Shim connection hygiene under abandoned client connections

The launcher shim and the shared desktop proxy MUST bound how long an accepted client connection may sit idle at the HTTP layer — waiting for a request line, completing a TLS handshake, or wedged on a socket write — and MUST reclaim the connection's thread and file descriptors when that bound elapses. Established blind CONNECT tunnels MUST remain exempt so long-lived idle tunnels such as the Remote Control websocket stay open. The shim process MUST raise its soft file-descriptor limit toward the platform ceiling at startup on a best-effort basis. Upstream connect retries MUST cover only the connect phase: once an upstream response is being copied to the client, a client-side failure MUST NOT re-send the upstream request, and the upstream response MUST be closed explicitly after copying.

#### Scenario: Client abandons a CONNECT tunnel before speaking

- **WHEN** a client opens a connection to the shim and sends nothing (or completes CONNECT and never starts the TLS handshake) for the idle bound
- **THEN** the shim closes the connection and reclaims its thread and file descriptors
- **AND** the shim's fd usage stays bounded regardless of how many such connections the client opens

#### Scenario: Idle Remote Control tunnel survives

- **WHEN** an established blind tunnel carries no traffic for longer than the idle bound
- **THEN** the tunnel remains open and usable

#### Scenario: Client hangs up mid-response

- **WHEN** the client connection fails while the shim is copying a completed upstream response
- **THEN** the shim does not re-send the upstream request
- **AND** the upstream response is closed
