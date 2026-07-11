## ADDED Requirements

### Requirement: Startup performance boundaries are measured independently

The project SHALL provide a repeatable benchmark tool that measures service startup-probe time, service readiness time, launcher preparation/proxy readiness, and command useful-output or completion time as distinct boundaries. The tool MUST use a monotonic clock, MUST retain individual samples and failures, and MUST NOT treat liveness as readiness.

#### Scenario: Service benchmark records startup and readiness separately

- **WHEN** an operator benchmarks an agent-lb process with startup and readiness probes
- **THEN** the result records process spawn-to-`/health/startup` and process spawn-to-`/health/ready` separately
- **AND** a readiness failure is not reported as a successful ready measurement merely because `/health` responds

#### Scenario: Arbitrary start command is measured

- **WHEN** an operator supplies a stable label and a command to the benchmark tool
- **THEN** the result records every sample, command completion or useful-output time, exit status, median, and p95
- **AND** raw prompts, credentials, authorization headers, and complete argument lists are not persisted

### Requirement: Startup benchmark history supports regression comparison

Benchmark runs MUST be serializable as versioned JSON records and appendable to a configurable history file. A comparison against a prior record MUST report median and p95 percentage change and MUST return a nonzero status when the configured p95 regression ceiling is exceeded.

#### Scenario: Current run regresses beyond the ceiling

- **WHEN** a current benchmark p95 exceeds a selected baseline p95 by more than the configured ceiling
- **THEN** the comparison reports the regression percentage and exits nonzero

#### Scenario: Benchmark record is attributable without leaking command content

- **WHEN** a benchmark record is written
- **THEN** it includes a stable label, timestamp, git revision when available, platform, Python version, sample count, and aggregate timings
- **AND** it does not persist raw prompts or secret-bearing environment values

### Requirement: Default database startup validates migration state without full drift comparison

Service startup MUST continue to fail closed when the configured database is behind the required Alembic head or a required migration fails. Full metadata-to-database schema drift comparison MUST remain available through the database check command and an explicit startup setting, but MUST NOT run on every default already-current service boot.

#### Scenario: Current database starts on the fast validation path

- **WHEN** the database revision is already at Alembic head
- **AND** full startup drift validation is not explicitly enabled
- **THEN** startup confirms migration state and proceeds without metadata-wide drift comparison

#### Scenario: Operator explicitly enables full startup drift validation

- **WHEN** the full startup drift setting is enabled
- **THEN** startup compares application metadata with the database after migrations
- **AND** detected drift fails startup according to the existing migration fail-fast policy

#### Scenario: Dedicated database check remains comprehensive

- **WHEN** an operator runs `agent-lb-db check`
- **THEN** migration state and full schema drift are validated regardless of the startup fast-path setting

### Requirement: Claude launchers minimize redundant startup work while preserving proof obligations

The normal Claude launcher MUST select an endpoint for interactive sessions using the readiness probe and MUST defer sticky account selection until the first proxied message so account and quota enrichment do not block the Claude UI. Headless sessions MUST retain eager session-route claiming because reset waiting and early account failure are part of their command contract. The ccdex launcher MUST require both a successful native token-count capability request and at least one active, routable OpenAI account before selecting a candidate, and MUST refuse to bypass agent-lb. An operator-configured remote-first preference MUST retain the local candidate as a fallback. The loopback intercepting proxy MUST be confirmed connectable before Claude is executed through the portable detached subprocess path.

#### Scenario: Interactive launcher reaches a ready local endpoint

- **WHEN** the first candidate passes `/health/ready`
- **THEN** the launcher selects it without synchronously claiming a session route or loading the account-summary banner
- **AND** the first proxied message performs the existing sticky selection through the normal proxy request path

#### Scenario: Headless launcher needs deterministic account selection

- **WHEN** `cc` is invoked with `-p` or `--print`
- **THEN** it eagerly claims the session route without a redundant preceding health request
- **AND** advertised reset waiting and account-selection failures remain visible before the command runs

#### Scenario: ccdex reaches a compatible local endpoint

- **WHEN** the first candidate returns a valid native token-count capability response
- **AND** its account summary contains an active, routable OpenAI account
- **THEN** ccdex selects it without a separate preceding health request
- **AND** an incompatible or unavailable endpoint is not treated as ready

#### Scenario: ccdex probes an empty local instance

- **WHEN** a local candidate implements native token counting but has no active OpenAI account
- **THEN** ccdex rejects that candidate and probes the next configured endpoint

#### Scenario: Laptop prefers the owner instance

- **WHEN** remote-first preference is enabled and the remote and local URLs differ
- **THEN** the remote candidate is probed first
- **AND** the local candidate remains available as the next fallback

#### Scenario: Proxy child becomes usable

- **WHEN** the launcher starts its loopback intercepting proxy
- **THEN** the launcher waits for both the ready marker and a successful loopback connection before executing Claude
- **AND** a startup timeout yields the existing safe fallback behavior for normal Claude and fail-closed behavior for ccdex
