## ADDED Requirements

### Requirement: First-event timeouts are account-neutral
When an upstream produces zero response events within the configured first-event window and the proxy raises `bridge_first_event_timeout`, the proxy MUST treat that timeout as account-neutral: it MUST NOT record an account-health error (MUST NOT increment the account's transient `error_count` or otherwise push the account toward error-backoff exclusion). The proxy MUST still steer the retry away from the timed-out account for that bridge session via the existing per-bridge-key cooldown and sticky-binding rebind. Other streaming failures that are genuinely account-attributable (upstream websocket drops, 5xx, rate-limit, quota, auth) MUST continue to record account health as before.

#### Scenario: first-event timeout does not penalize the account
- **WHEN** an upstream produces no response event within the first-event window and the proxy raises `bridge_first_event_timeout`
- **THEN** the account's transient error count is not incremented
- **AND** the account is not pushed toward error-backoff exclusion by this timeout
- **AND** the per-bridge-key cooldown still excludes the account from the next bridge creation for that session

#### Scenario: genuine transient stream faults still penalize the account
- **WHEN** a streamed response fails with an account-attributable transient code such as `stream_idle_timeout`
- **THEN** the proxy records a transient account-health error for routing

### Requirement: Compaction fails over across the codex pool
A compaction Responses request (`/v1/responses/compact`, `/backend-api/codex/responses/compact`) MUST attempt account failover across more than two accounts before returning `no_accounts`, so a transient-error burst on the first selected accounts does not strand the request while other healthy accounts remain untried. Each failover attempt MUST select a distinct account (excluding already-tried accounts).

#### Scenario: compaction tries additional accounts before failing
- **WHEN** a compaction request's first two selected accounts return transient failures
- **AND** the codex pool still has untried healthy accounts
- **THEN** the proxy selects and tries those additional distinct accounts before returning `no_accounts`
