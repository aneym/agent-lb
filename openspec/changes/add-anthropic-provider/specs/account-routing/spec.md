## ADDED Requirements

### Requirement: Anthropic selection uses quota-scoped eligibility
Anthropic Messages proxy selection SHALL classify each request into an Anthropic quota key before selecting an account. At minimum the quota keys SHALL distinguish lower-tier standard model requests from top-model requests and top-model requests with thinking enabled. An upstream Anthropic `429` for one quota key SHALL persist a cooldown for that account and quota key without globally marking the account unavailable for all Anthropic traffic.

#### Scenario: Top-model 429 fails over without global account disablement
- **GIVEN** two active Anthropic OAuth accounts
- **AND** one account is sticky for a top-model thinking request
- **WHEN** that account returns an upstream `429` with reset evidence
- **THEN** agent-lb records a cooldown for the top-model-thinking quota key on that account
- **AND** it retries another eligible Anthropic account for the same request
- **AND** it does not change the first account's global status to `rate_limited`

#### Scenario: All accounts cooling down for a quota fail fast
- **GIVEN** every active Anthropic account has an active cooldown for the requested quota key
- **WHEN** Claude Code sends a request for that quota key
- **THEN** agent-lb returns a structured retryable error with reset evidence
- **AND** it does not leave the client waiting for an unavailable account

### Requirement: Claude Code launcher preserves subscription billing
The local Claude Code load-balanced launcher SHALL route requests by setting `ANTHROPIC_BASE_URL` to agent-lb only. It SHALL NOT set `ANTHROPIC_AUTH_TOKEN`, inject an API key, or add a default `--model` override. Claude Code's own selected model, effort, and Claude Max/OAuth billing mode MUST remain visible to the client.

#### Scenario: Default Claude Code model is preserved
- **WHEN** an operator starts Claude Code through the load-balanced launcher without passing `--model`
- **THEN** Claude Code keeps its own default best model and effort
- **AND** the UI reports Claude Max/OAuth billing rather than API usage billing
