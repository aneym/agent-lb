# Codex network cap

## ADDED Requirements

### Requirement: opted-in Codex processes share one aggregate cap

The launcher MUST route every future opted-in Codex process through one
user-scoped proxy daemon and one shared byte scheduler. The scheduler MUST
account for the sum of bytes forwarded in both directions, MUST use a bounded
burst, MUST NOT exempt small writes, and MUST provide FIFO admission at a
bounded I/O quantum.

#### Scenario: concurrent bulk transfers

- **WHEN** two or more opted-in clients transfer more than the configured burst
- **THEN** their combined steady-state forwarded bytes do not exceed the configured rate plus measurement tolerance
- **AND** a tiny request is admitted without waiting behind an entire bulk transfer

### Requirement: the proxy preserves supported transport semantics

The proxy MUST support authenticated HTTP/1.1 absolute-form forwarding and
blind CONNECT tunneling. It MUST preserve bidirectional bytes and TCP
half-closes, bound request-header reads, close plain HTTP client connections
when the upstream response closes, and release handlers after cancellation.

#### Scenario: HTTP and CONNECT clients complete normally

- **WHEN** an authenticated client sends an ordinary HTTP request or opens a CONNECT tunnel
- **THEN** the upstream bytes are forwarded without an HTTP 405 response
- **AND** EOF, cancellation, and half-close terminate the corresponding handler

### Requirement: daemon election cannot create independent live buckets

Daemon startup MUST be serialized. A launcher MUST reuse the recorded daemon
when its PID is live and its authenticated health check succeeds. If that PID
is live but the health check fails, the launcher MUST fail closed and MUST NOT
start a second daemon.

#### Scenario: recorded live daemon is temporarily unavailable

- **WHEN** the recorded daemon PID is alive but its authenticated health request fails
- **THEN** the launcher exits with an error
- **AND** it does not remove the state or start another daemon

### Requirement: scope remains explicit

The launcher MUST bind only to loopback, require an unguessable local
credential, preserve inherited proxy settings by refusing to replace them, and
add loopback names to `NO_PROXY`. It MUST NOT claim coverage for existing
processes, Codex Desktop, proxy-bypassing clients, or Agent LB loopback
inference.

#### Scenario: an opted-in process launches

- **WHEN** the launcher has no inherited proxy configuration
- **THEN** it exports authenticated loopback HTTP and HTTPS proxy variables
- **AND** loopback Agent LB destinations remain in `NO_PROXY`
