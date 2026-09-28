## ADDED Requirements

### Requirement: Sol is the default implementer; Sonnet high stands in when Codex is empty

The routing table MUST lead the implement and mechanical chains with the seat named by `implement_default` (`gpt-implementer`, sol-latest, medium), and MUST keep `sonnet-implementer` (sonnet-latest, high) next in each chain, recorded as the Codex-empty fallback ahead of opus-seat. A pool percentage or headroom level MUST NOT move implementation off Sol; only the seat recorded down, its pool with no eligible account, or the fold's re-seat after two infra failures (429s or usage-limit errors) does. A pool counts as exhausted only when it has no eligible account.

#### Scenario: Sol leads by default

- **WHEN** `route pick implement` runs with both pools ok, or with the Codex pool low
- **THEN** it picks `gpt-implementer`, audited by the Opus `verifier`, with `sonnet-implementer` as its first fallback

#### Scenario: Sol is out

- **WHEN** `gpt-implementer` is recorded down in routing state after a 429 or usage-limit error
- **THEN** `route pick implement` picks `sonnet-implementer`, audited by `codex-verifier` on sol-latest

#### Scenario: Default switch

- **WHEN** `implement_default` is set to `sonnet-implementer`
- **THEN** `route pick implement` picks `sonnet-implementer`, with `gpt-implementer` as its first fallback
