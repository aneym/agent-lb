## ADDED Requirements

### Requirement: Upload admission fails fast when the queue cannot drain in time

While the upload throttle is on, upload admission MUST estimate the wait before queueing a request. The estimate is `max(0, backlog + need - cap) / rate`, where `budget` is `rate` times 4 seconds, `cap` is 80% of `budget` for high-priority uploads and `budget` for batch, and `need` is `min(nbytes, cap)`. `backlog` is the bytes already outstanding plus the queued bytes the new request would sit behind. For high-priority uploads that backlog is high outstanding bytes plus queued high bytes. For batch uploads it is all outstanding bytes plus all queued bytes, except high outstanding counts as at most 80% of `budget` so batch keeps its 20% share. When the estimate is greater than `AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS`, admission MUST reject immediately, without sleeping, with `UploadAdmissionRejected`. The default ceiling MUST be 30 seconds. The first request on an empty queue MUST still be admitted when outstanding bytes for its class are zero, including when its own body is larger than the budget. A request that is eligible to wait and still exceeds the ceiling MUST raise `UploadAdmissionTimeout` carrying the same rate, backlog, projected wait, ceiling, and class, plus the seconds actually waited. Either failure MUST log one warning naming the class, rate in KB/s, backlog in MB, projected or waited seconds, the ceiling, and a hashed session, and MUST NOT log the raw session key. Anthropic request logs MUST record `upload_admission_rejected` for the immediate reject and `upload_admission_timeout` for the waited-out path, and the HTTP response MUST be 503 with that same code and a message that names the rate, backlog, and seconds. With the throttle off, admission MUST remain a no-op.

#### Scenario: A hopeless queue is rejected immediately

- **GIVEN** the upload throttle is on
- **AND** an admitted upload already holds enough outstanding bytes that a second request's projected wait exceeds the ceiling
- **WHEN** the second request asks for admission
- **THEN** admission raises `UploadAdmissionRejected` without waiting
- **AND** the error carries the throttle rate, the backlog bytes, the projected wait, the ceiling, and the upload class

#### Scenario: The default ceiling is 30 seconds

- **GIVEN** `AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS` is unset
- **WHEN** a request is eligible to wait and is still waiting when the ceiling expires
- **THEN** admission raises `UploadAdmissionTimeout` after 30 seconds
- **AND** the error carries the seconds waited along with the rate, backlog, projected wait, ceiling, and class

#### Scenario: The failure names the numbers

- **WHEN** admission rejects or times out a request
- **THEN** one warning names the class, rate in KB/s, backlog in MB, the projected or waited seconds, the ceiling, and a hashed session
- **AND** the Anthropic request log uses `upload_admission_rejected` for an immediate reject and `upload_admission_timeout` for a waited-out request
- **AND** the client receives HTTP 503 whose code matches that error code and whose message names the rate, backlog, and seconds
