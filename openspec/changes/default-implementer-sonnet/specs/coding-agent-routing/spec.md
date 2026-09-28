## ADDED Requirements

### Requirement: Sonnet is the default implementer, Sol its fallback

The routing table MUST lead the implement and mechanical chains with the seat named by `implement_default`, and MUST keep both `sonnet-implementer` (sonnet-latest, high) and `gpt-implementer` (sol-latest, medium) in each chain. A pool percentage or headroom level MUST NOT move implementation off Sonnet; in `route` only the seat recorded down, its pool with no eligible account, or its Sol reviewer's pool at one eligible account (rule 1) does, and the fold's re-seat after two infra failures (429s or usage-limit errors) does in a run. A pool counts as exhausted only when it has no eligible account.

#### Scenario: Sonnet leads by default

- **WHEN** `route pick implement` runs with both pools ok, or with the Anthropic pool low
- **THEN** it picks `sonnet-implementer`, audited by `codex-verifier` on sol-latest

#### Scenario: Sonnet is out

- **WHEN** `sonnet-implementer` is recorded down in routing state after a 429 or usage-limit error
- **THEN** `route pick implement` picks `gpt-implementer`, audited by the Opus `verifier`

#### Scenario: One-line revert

- **WHEN** `implement_default` is set to `gpt-implementer`
- **THEN** `route pick implement` picks `gpt-implementer`, with `sonnet-implementer` as its first fallback
