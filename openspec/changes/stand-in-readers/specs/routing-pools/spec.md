## ADDED Requirements

### Requirement: Route reports sessions standing in
The route CLI SHALL read `/api/pools/stand-ins` once per pick or stand-ins command with a default timeout of 2 seconds. Failed or malformed responses SHALL yield empty active and recent lists without preventing routing. The pick JSON SHALL include a standing_in list containing session_id, lane, intended, running, since, and expected_return for each active record in endpoint order.

#### Scenario: A picker sees the stand-in and expected return
- **GIVEN** the endpoint reports an active session running a stand-in model
- **WHEN** `route pick plan --json` runs
- **THEN** standing_in contains the six specified fields of that active record
- **AND** text pick output shows `standing in: <lane or session_id> on <running> for <intended> since <since>, back ~<expected_return or unknown>`

### Requirement: Route lists active and recent stand-ins
The `route stand-ins --json` command SHALL print the endpoint body unchanged. Text output SHALL print the same standing-in line for each active record in endpoint order, followed by `recent: <n> returned`. The command SHALL exit zero even if the endpoint is missing.

#### Scenario: An older service lacks the endpoint
- **GIVEN** the endpoint returns HTTP 404
- **WHEN** a pick or stand-ins command runs
- **THEN** the stand-in lists are empty
- **AND** stand-ins exits zero
