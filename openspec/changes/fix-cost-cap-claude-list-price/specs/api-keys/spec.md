## ADDED Requirements

### Requirement: Cost caps meter Claude requests at list price

API key `cost_usd` limits MUST meter every request at the model's list price. Claude models MUST be priced from the Anthropic price table using input, output, cache-creation (at the 5-minute or 1-hour rate the request used) and cache-read tokens. The actual spend of a subscription account, which is $0 per request, MUST NOT reduce the metered amount.

#### Scenario: Capped Claude caller trips at list price

- **WHEN** a key with a `cost_usd` cap of M microdollars sends Claude requests that each cost C microdollars at list price
- **THEN** the first ceil(M / C) requests are served
- **AND** the next request is refused with a rate-limit error naming the `cost_usd` limit

#### Scenario: Uncapped traffic is unchanged

- **WHEN** a key without a `cost_usd` cap, or a keyless trusted client, sends Claude requests
- **THEN** the requests are served as before

### Requirement: Unpriced models are refused under a cost cap

When a `cost_usd` limit applies to a request and the requested model has no list price, the system MUST refuse the request before any upstream call with HTTP 403 and code `model_unpriced_under_cost_cap`, and the message MUST name the model and the cap. Usage that cannot be priced at settlement MUST keep its reservation rather than settle at $0.

#### Scenario: Unpriced model under a cap

- **WHEN** a key with a `cost_usd` cap requests a model absent from every price table
- **THEN** the system answers 403 naming the model and the cap
- **AND** no upstream request is sent and no usage is metered

#### Scenario: Unlisted Claude version under a cap

- **WHEN** a key with a `cost_usd` cap requests a Claude name that only a wildcard alias would price, such as `claude-sonnet-4-99` or `claude-opus-5-99`
- **THEN** the model counts as unpriced and the request is refused with 403 `model_unpriced_under_cost_cap`
- **AND** a listed model with only a dated snapshot, `-latest` or a bracketed context tag after its name (such as `claude-haiku-4-5-20251001` or `claude-opus-5-5[1m]`) stays priced

#### Scenario: Blank model under a cap

- **WHEN** a key with a `cost_usd` cap sends a request whose model is the empty string
- **THEN** the request is refused with 403 `model_unpriced_under_cost_cap` before any upstream call

#### Scenario: Binary websocket response.create under a cap

- **WHEN** a websocket client with a capped key sends a `response.create` as a binary frame after the upstream session is open
- **THEN** the frame is prepared, reserved and refused exactly like a text `response.create`, and an unpriced model never reaches upstream

#### Scenario: Unpriced model without a cost cap

- **WHEN** a key with no `cost_usd` limit requests a model absent from every price table
- **THEN** the request is served as before

#### Scenario: Unlisted OpenAI model under a cap

- **WHEN** a capped caller requests `gpt-5.99`, `gpt-5.1-unpriced`, or literal wildcard names `gpt-5*` and `gpt-5.5*`
- **THEN** admission refuses it with `model_unpriced_under_cost_cap` before upstream
- **AND** only exact OpenAI price entries or explicitly named aliases qualify, not wildcard or prefix matches

#### Scenario: File operations do not consume model spend

- **WHEN** a cost-capped key registers or finalizes a file on the file routes
- **THEN** cost limits do not reject the operation, even when exhausted, while non-cost limits still apply
- **AND** `files-create` and `files-finalize` as model names on model routes remain unpriced and refused
- **AND** model requests with zero estimated tokens reserve at least one microdollar
