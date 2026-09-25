## ADDED Requirements

### Requirement: The shared HTTP pool does not cap connections per host

The shared outbound HTTP session MUST NOT limit connections per upstream host by
default. Its total connection limit MUST default to 1024, as a guard for the
process's file-descriptor budget. Operators MAY set either limit through the
environment.

#### Scenario: Hundreds of streams to one provider host

- **GIVEN** the default connector settings
- **WHEN** 200 requests to one host are held open at once through the shared session
- **THEN** all 200 are connected at the same time, and none waits for a pool slot

### Requirement: Paced uploads do not hold the event loop

When the upload cap is on, the paced transport MUST release bytes in chunks of at
least one TLS record (16 KiB, or the whole write if smaller), and MUST sleep
rather than write when fewer tokens are available. The average rate MUST stay at
the configured cap.

#### Scenario: A paced upload runs beside other work

- **GIVEN** the upload cap is on at 500 KB/s
- **WHEN** a 400 KB request body is uploaded
- **THEN** event-loop lag stays under 100 ms for the whole upload

### Requirement: Edge-rejected websocket handshakes are retried

When an upstream Responses websocket handshake is refused with HTTP 403 and the
body is not an OpenAI error payload, the client MUST retry the handshake with
jittered exponential backoff, for up to three minutes after the first refusal,
with no single wait longer than 30 seconds. If the handshake is still refused when
that window ends, or when the request's own time budget would run out first, the 403
MUST be surfaced as before. A 403 that carries an OpenAI error payload MUST be
surfaced on the first answer, without a retry. This holds on both direct websocket
paths: the Codex websocket and HTTP bridge connection, and a streamed Responses
request sent upstream over websocket.

#### Scenario: A burst limit clears

- **GIVEN** the edge refuses the first eight handshakes with a bare 403
- **WHEN** a Responses websocket is opened
- **THEN** the ninth handshake succeeds, and the caller gets a connected websocket

#### Scenario: A refusal that never clears

- **GIVEN** the edge refuses every handshake with a bare 403
- **WHEN** a Responses websocket is opened
- **THEN** the caller gets the 403 once the retry window has passed

#### Scenario: An account-level refusal

- **GIVEN** the upstream refuses the handshake with 403 and a JSON error payload
- **WHEN** a Responses websocket is opened
- **THEN** the caller gets that error after one handshake

#### Scenario: A streamed Responses request meets a burst limit

- **GIVEN** the upstream stream transport is websocket and the edge refuses the first eight handshakes with a bare 403
- **WHEN** a Responses request is streamed
- **THEN** the ninth handshake succeeds, and the stream completes without a failure event
