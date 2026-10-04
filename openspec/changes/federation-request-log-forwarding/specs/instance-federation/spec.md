# instance-federation — delta

## ADDED Requirements

### Requirement: Owner accepts forwarded request logs

The federation router SHALL expose `POST /api/federation/request-logs`, protected by the same federation Bearer token as `/mirror`. The body carries `instance_id` matching `^[A-Za-z0-9_.:-]{1,64}$` and `rows` of at most 500 request-log copies. A larger batch MUST be rejected with 413 or 422 and MUST store nothing. `caller_user` (32), `caller_user_source` (32), `caller_machine` (48), `caller_machine_source` (16), `caller_seat` (64), and `room` (128) MUST be rejected above those lengths, which match the `request_logs` columns. The mirror MUST truncate those fields to the same lengths before sending so one over-long row cannot stall the cursor on a 422. Each stored row MUST set `source` to `edge:<instance_id>`, MUST set `api_key_id` to null, MUST keep `account_id` only when that account exists locally and otherwise store null, and MUST copy every other forwarded column. `id` and `deleted_at` are not forwarded. A row whose `request_id` and `source` already exist MUST be skipped, using one existence query per batch. The response MUST be `{accepted, skipped, max_source_row_id}`. Re-delivery of the same batch MUST NOT create duplicate rows.

#### Scenario: Batch is stored once

- **GIVEN** Studio has account A and no account B
- **WHEN** a mirror posts a batch containing one row for A and one row for B with a valid mirror token
- **THEN** both rows are stored with source `edge:<instance_id>` and null `api_key_id`
- **AND** the row for A keeps account A and the row for B has a null account
- **AND** posting the same batch again skips both rows

#### Scenario: Peer auth is required

- **WHEN** `POST /api/federation/request-logs` is called without a valid federation Bearer token
- **THEN** the response is 403 and nothing is stored

### Requirement: Mirror forwards request logs after a successful pull

A mirror (peer URL and mirror token configured) SHALL, on each successful mirror cycle, send local `request_logs` rows with `id` greater than its cursor, deleted rows omitted, in ascending id order, in batches of at most 500, and at most 10 batches per cycle, to the peer's request-logs endpoint using the mirror token. The cursor file `<AGENT_LB_DATA_DIR>/federation-request-log-cursor.json` MUST be replaced only after a 2xx, with the highest `source_row_id` in the batch that was sent. With no cursor file, the mirror MUST start from rows requested in the last 48 hours. A forward failure MUST NOT fail the mirror pull or the usage-report push, and MUST leave the cursor unchanged. Setting `federation_forward_request_logs` to false (env `AGENT_LB_FEDERATION_FORWARD_REQUEST_LOGS`) MUST turn forwarding off. An instance with no peer MUST NOT forward.

#### Scenario: Cursor advances only after success

- **GIVEN** a mirror with rows newer than its cursor
- **WHEN** the peer accepts a batch with 2xx
- **THEN** the cursor file becomes the highest id in that batch
- **AND** a later 5xx leaves the cursor unchanged
- **AND** the mirror pull is still successful

#### Scenario: First run is bounded

- **GIVEN** a mirror with no cursor file, a row from 49 hours ago, and a row from the last hour
- **WHEN** it forwards
- **THEN** the recent row is sent and the 49-hour row is not

#### Scenario: Cursor ahead of the table uses the lookback

- **GIVEN** a cursor greater than every `request_logs` id, a row from 49 hours ago, and a row from the last hour
- **WHEN** the mirror forwards
- **THEN** it logs a warning, sends the recent row, and does not send the 49-hour row
- **AND** the cursor file becomes the recent row's id

### Requirement: Local usage rollups omit forwarded edge rows

`FederationRepository.list_local_usage_rollups` MUST exclude request logs whose `source` starts with `edge:`. Readers that feed routing, API-key limits, member caps, or quota-planner demand MUST exclude those rows. Readers that are audits, attribution, or request lists MUST include them.

#### Scenario: Local rollup does not recount edge copies

- **GIVEN** one local request log and one log with source `edge:ax42` for the same account and day
- **WHEN** the local usage rollup is computed
- **THEN** the rollup counts only the local log
