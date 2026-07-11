# proxy-admission-control Delta

## ADDED Requirements

### Requirement: Client-facing websockets have no pong deadline

The server MUST run its client-facing websocket protocol with transport pings
enabled and the pong deadline disabled (`ws_ping_interval=20.0`,
`ws_ping_timeout=None`), matching the upstream websocket leg's policy: the
service's own request and idle budgets decide when a turn has stalled, not the
transport pong watchdog. A connected agent client that blocks its event loop
longer than the ping interval MUST NOT be disconnected for a missed pong.

#### Scenario: Agent client stalls during long local work

- **GIVEN** a client websocket session with an in-flight turn
- **WHEN** the client does not answer transport pings for more than 20 seconds
- **THEN** the connection is not closed with 1011 "keepalive ping timeout"
