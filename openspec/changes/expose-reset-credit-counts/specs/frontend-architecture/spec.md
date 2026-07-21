## ADDED Requirements

### Requirement: Accounts payload exposes banked reset-credit counts

`GET /api/accounts` MUST include an optional `resetCreditsAvailable` integer per account: the most recently cached available reset-credit count for OpenAI accounts, and `null` when the count has not been observed or the account is not an OpenAI account. The field MUST be served from the cache without upstream calls on the accounts-list path.

#### Scenario: OpenAI account with an observed count

- **WHEN** the cache holds a count for an OpenAI account
- **THEN** the account's summary carries that integer in `resetCreditsAvailable`

#### Scenario: Unknown or non-OpenAI account

- **WHEN** no count has been observed for an account, or the account belongs to another provider
- **THEN** `resetCreditsAvailable` is `null`
