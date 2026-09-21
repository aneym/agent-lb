## ADDED Requirements

### Requirement: OpenAI-first seat ranking
The router SHALL rank implementer Terra medium, implementer Sol medium, Cursor Sol high, GLM, then Opus for implementation; difficulty at least four SHALL begin with Sol. Astra SHALL be first for verify and review, with Opus second for review. Explore SHALL rank Cursor Grok low, Sonnet, then Opus. Plan SHALL remain the Fable driver. Mechanical, research, computer and council rankings SHALL remain unchanged.

#### Scenario: Money-path implementation
- **WHEN** a task is flagged for auth, RLS, billing, migrations, receipts, idempotency or tool boundaries
- **THEN** Astra validation and a second read by claude-opus-5 SHOULD accompany the implementation pick when their pools admit
- **AND** unavailable verifier pools SHALL return the implementer with a null verifier, a named reason, and warnings without blocking the decision

### Requirement: Fresh account reserve admission
A pool SHALL admit a class only when at least one account is active, has no current rate-limit reset, reports at least one usage window, and has every reported finite remaining percentage strictly above the reserve, default 20 percent and configurable with --reserve. A window with both a null percentage and null reset SHALL be not applicable. The router SHALL read /api/accounts, never admit from aggregate headroom, and SHALL refresh or refuse state older than 300 seconds with a named reason. Accounts with no reported window data or an unknown reported window percentage SHALL NOT admit a pool. Output SHALL identify the limiting account and window. Pools SHALL display usable-now and minimum known short-window percent, and blocked when usable-now is zero.

#### Scenario: Healthy weekly headroom hides a blocked short window
- **WHEN** all accounts fail status or either reserve gate
- **THEN** the pool SHALL be blocked regardless of weekly headroom

#### Scenario: Refresh fails with stale state
- **WHEN** account refresh fails and cached state is older than 300 seconds
- **THEN** pick SHALL fail with a stale-state reason and SHALL NOT use cached pool aggregates

### Requirement: Typed task classification
Classify SHALL send only task text and path names in one TypeSafe System One ask containing class choice, difficulty score 1-5, money_path noul and needs_write noul. It SHALL output class, seat, model, effort, required verifiers, limiting window and Jev confidence. Jev unavailability SHALL exit 3 with a reason, without retrying. Dispatch-line SHALL print one class/seat/model/effort header and append the decision in dispatch.jsonl-compatible shape to an injectable path.

#### Scenario: Jev is unavailable
- **WHEN** the typed service is unavailable
- **THEN** classify and dispatch-line SHALL exit 3 with JEV UNAVAILABLE and SHALL NOT emit a successful decision

### Requirement: Anthropic telemetry is visible
The routing pulse SHALL count Fable, Opus and Sonnet requests and SHALL emit one telemetry-missing line on analytics failure. Policy and guard text SHALL describe Opus as a scarce cross-vendor read, not an unrationed implementation default.

#### Scenario: Analytics fails
- **WHEN** analytics cannot supply request counts
- **THEN** the pulse SHALL report telemetry missing instead of silently implying no spend
