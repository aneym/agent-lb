# Account Routing — rate-limit retry metadata and automatic resume

## ADDED Requirements

### Requirement: Messages endpoint surfaces structured rate-limit retry metadata

When `/v1/messages` cannot serve a request because all Anthropic accounts are
exhausted (quota cooldown or selection failure) and the earliest reset time is
known, the proxy MUST respond before the stream starts with HTTP 429, an
Anthropic-native envelope `{"type":"error","error":{"type":"rate_limit_error",
"message":...}}`, a `retry-after` header (seconds until reset), and an
`anthropic-ratelimit-unified-reset` header (Unix epoch seconds of the reset).

#### Scenario: All accounts cooling down for the requested quota

- **GIVEN** every Anthropic account has an active cooldown for the requested quota key with a known future reset
- **WHEN** a client posts to `/v1/messages`
- **THEN** the response status is 429
- **AND** the body error type is `rate_limit_error`
- **AND** the `retry-after` and `anthropic-ratelimit-unified-reset` headers reflect the earliest reset

#### Scenario: Exhaustion with no known reset

- **GIVEN** no Anthropic account is selectable and no reset time is known
- **WHEN** a client posts to `/v1/messages`
- **THEN** the proxy responds with the existing non-429 error envelope and MUST NOT fabricate retry headers

### Requirement: Mid-stream failures emit a structured error event

If account selection retries are exhausted after the `/v1/messages` response has
started, the proxy MUST emit a structured error instead of truncating the body:
an SSE `event: error` block carrying the Anthropic-native error envelope for
streaming requests, or the JSON error envelope for non-streaming requests.

#### Scenario: Upstream rate-limits every retry attempt mid-stream

- **GIVEN** the first account was selected successfully and every upstream attempt returns 429
- **WHEN** the proxy exhausts its selection attempts inside the response body
- **THEN** a streaming client receives an SSE `event: error` block whose data is the Anthropic-native error envelope

#### Scenario: Non-streaming upstream overload is returned before response headers

- **GIVEN** an Anthropic account is selected and upstream returns HTTP 529 with an `overloaded_error` envelope before any successful response bytes
- **WHEN** a non-streaming client posts to `/v1/messages`
- **THEN** the proxy responds with HTTP 529
- **AND** the response body preserves the Anthropic-native `overloaded_error` type and exact upstream message
- **AND** the proxy MUST NOT return HTTP 200 with a JSON error body

### Requirement: Session-route errors carry retry timing

When `/api/anthropic/session-route` rejects a claim because accounts are
exhausted and the earliest reset is known, the error envelope MUST include
`error.retryAt` (RFC 3339 UTC) and `error.retryAfterSeconds` (non-negative
integer), and the response MUST include a `retry-after` header.

#### Scenario: Claim rejected while accounts cool down

- **GIVEN** all Anthropic accounts have an active cooldown with a known future reset
- **WHEN** the launcher posts to `/api/anthropic/session-route`
- **THEN** the error envelope contains `retryAt` and `retryAfterSeconds`
- **AND** the response carries a `retry-after` header

### Requirement: Anthropic routing prefers the expiring primary window

When the dashboard setting `preferEarlierResetAccounts` is enabled, Anthropic
`/v1/messages` account selection MUST pass reset preference into the shared
load balancer with the `primary` reset window, because Claude's short 5-hour
limit is the quota that should be depleted before it refreshes. Within the
earliest primary reset window, the load balancer MUST prefer the account with
more primary capacity remaining before considering secondary-window pressure.

#### Scenario: Claude message selection honors primary reset preference

- **GIVEN** dashboard reset preference is enabled
- **WHEN** a client posts to `/v1/messages`
- **THEN** Anthropic account selection uses `prefer_earlier_reset_accounts=true`
- **AND** it uses `prefer_earlier_reset_window=primary`
- **AND** it keeps the Anthropic `usage_weighted` routing strategy
- **AND** ties inside the earliest primary reset window are ranked by primary-window headroom before secondary-window headroom

### Requirement: Upstream reset headers parse epoch and RFC 3339 forms

The proxy MUST accept upstream `anthropic-ratelimit-*-reset` header values in
RFC 3339 form and in Unix-epoch form (seconds or milliseconds) when recording
cooldown reset times.

#### Scenario: Upstream sends epoch-seconds unified reset

- **GIVEN** an upstream 429 with `anthropic-ratelimit-unified-reset` set to a Unix epoch in seconds
- **WHEN** the proxy records the quota cooldown
- **THEN** the stored reset time equals that epoch

### Requirement: Launcher waits for advertised resets and resumes headless runs

The Claude launcher MUST, when a session-route claim fails with retry timing and
waiting is enabled (`CLAUDE_LB_WAIT_FOR_LIMIT` =`auto` default, `always`, or
`never`; bounded by `CLAUDE_LB_WAIT_MAX_SECONDS`), wait until the advertised
reset plus a small buffer and re-claim before launching. For headless (`-p`)
invocations with auto-resume enabled (`CLAUDE_LB_AUTO_RESUME`, default on), the
launcher MUST inject a stable `--session-id`, and after a non-zero Claude exit
that coincides with LB-confirmed account exhaustion MUST wait for the advertised
reset and relaunch with `claude --resume <session-id>`, bounded by a maximum
attempt count and the wait cap.

#### Scenario: Preflight claim fails with retryAt inside the cap

- **GIVEN** session-route returns an error with `retryAfterSeconds` below the wait cap and waiting is enabled
- **WHEN** the launcher starts
- **THEN** it prints a human-readable waiting message with the local reset time and re-claims after the reset instead of failing

#### Scenario: Headless run interrupted by exhaustion

- **GIVEN** a headless run exits non-zero and a follow-up session-route probe confirms exhaustion with retry timing
- **WHEN** auto-resume is enabled and attempts remain
- **THEN** the launcher waits for the reset and relaunches `claude --resume` with the same session id

#### Scenario: Non-rate-limit failure is not retried

- **GIVEN** a headless run exits non-zero and the session-route probe succeeds (accounts available)
- **WHEN** the launcher evaluates auto-resume
- **THEN** it exits with the original exit code without retrying
