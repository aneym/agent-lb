## ADDED Requirements

### Requirement: Account-model incompatibility fails over
When the upstream rejects a request because the selected account's plan does not
support the requested model, the proxy SHALL retry the request on another
account before any output reaches the client, SHALL NOT record an account-health
error for that rejection, and SHALL skip that account for that model during
selection for a bounded TTL.

#### Scenario: One account lost the model
- **GIVEN** two active OpenAI accounts and the first rejects `gpt-6-sol` with "model is not supported when using Codex with a ChatGPT account"
- **WHEN** a client sends a `gpt-6-sol` request that is routed to the first account
- **THEN** the request completes on the second account and the client never receives the rejection
- **AND** later `gpt-6-sol` selections skip the first account until the TTL lapses
- **AND** the first account stays selectable for other models

#### Scenario: No account supports the model
- **GIVEN** every account has rejected the model within the TTL
- **WHEN** a client requests the model
- **THEN** selection still returns an account so the client receives the upstream error rather than a local no-accounts error
