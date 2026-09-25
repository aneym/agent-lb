## ADDED Requirements

### Requirement: Claude Code billing-first payloads are forwarded unchanged

When a Messages request's first system block is a Claude Code billing block (text starting `x-anthropic-billing-header:`), the proxy MUST treat the payload as a Claude Code payload and MUST NOT insert, remove, reorder, or rewrite any system block. The identity line is only added to payloads whose first system block is neither the Claude Code identity nor a billing block.

#### Scenario: Consecutive requests carry different billing values

- **WHEN** two requests in one session lead with billing blocks whose `cch` values differ, followed by the Claude Code identity or the Agent SDK line
- **THEN** each request is forwarded with exactly the system blocks it arrived with
- **AND** no identity block is prepended ahead of the billing block

#### Scenario: Teammate payloads without a prompt id

- **WHEN** a billing-first request carries no `cc_prompt_id`
- **THEN** it is forwarded unchanged like any other billing-first request
