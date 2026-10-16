## ADDED Requirements

### Requirement: Agents call Muse through agent-lb without their own Meta credential
`POST /muse/v1/responses` SHALL forward an OpenAI Responses request to the Muse upstream with the serving host's Muse login as the bearer, SHALL drop the client's `Authorization`, `x-api-key` and `cookie` headers and every `x-agent-lb-*` header, and SHALL retry once with a re-read credential when the upstream answers 401 and the re-read credential differs.

#### Scenario: Pi agent with a placeholder key
- **GIVEN** the host has a Muse login and a client sends `Authorization: Bearer agent-lb`
- **WHEN** it posts a Responses request for `muse-latest`
- **THEN** the upstream sees the host's Muse key and the pinned `muse-latest` model, and the client gets the upstream response

### Requirement: Contributor mode stays off on the provider route
The route SHALL refuse a model ending in `-contributor` with 400 `contributor_mode_refused`, a model not starting with `muse-` with 400 `unknown_muse_model`, and a host without a Muse login with 503 `muse_credential_unavailable`, without calling the upstream.

#### Scenario: Contributor model
- **WHEN** a client asks for `muse-wonder-1.0-contributor`
- **THEN** agent-lb answers 400 `contributor_mode_refused` and makes no upstream call

### Requirement: Muse usage is metered per agent with no cap
Every provider call SHALL write one request log row with provider `muse`, transport `muse-provider`, the resolved model, input and output tokens from the response usage (streamed or not), and the caller seat from `x-agent-lb-seat`. The route SHALL NOT apply a spend or token cap.

#### Scenario: Sunday's call is attributed
- **GIVEN** a request with `x-agent-lb-seat: sunday`
- **WHEN** it completes
- **THEN** a `muse` request log row with `caller_seat = sunday` and its token counts exists
