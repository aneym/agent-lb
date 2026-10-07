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

#### Scenario: Unpriced model without a cost cap

- **WHEN** a key with no `cost_usd` limit requests a model absent from every price table
- **THEN** the request is served as before
