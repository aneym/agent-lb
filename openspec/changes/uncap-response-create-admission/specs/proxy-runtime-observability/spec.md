## ADDED Requirements

### Requirement: Startup runtime fingerprint is logged
On service startup, the runtime MUST emit a low-cardinality diagnostic
fingerprint that lets an operator identify the running instance posture without
needing to infer it from a wrapper banner or plist. The fingerprint MUST include
database backend, data directory, dashboard auth mode, trusted-proxy CIDR count,
unauthenticated proxy CIDR count, whether a Tailnet unauthenticated proxy CIDR is
configured, metrics enablement and Prometheus availability, metrics bind target,
account-local response-create and stream caps, HTTP bridge enablement, HTTP
bridge queue/session/concurrency posture, and HTTP bridge schema status. The
fingerprint MUST NOT log raw database URLs, API keys, account tokens,
authorization headers, prompt text, request payloads, raw affinity keys, or
account secrets.

#### Scenario: startup log explains local breakage posture
- **WHEN** the service finishes startup database/schema checks
- **THEN** the console log includes an `agent_lb_runtime_fingerprint` event with
  safe runtime posture fields
- **AND** the log identifies whether the service is running against SQLite or
  PostgreSQL without emitting the database URL credentials or host
- **AND** the log identifies trusted-proxy, metrics, cap, and HTTP bridge
  posture without exposing request payloads or credentials
