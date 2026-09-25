## MODIFIED Requirements

### Requirement: Locked Sol execution profile

Every compatibility inference request MUST use a served GPT model and requested service tier `priority`. A request naming a served `gpt-*` model, or a family alias (`sol-latest`, `luna-latest`) that resolves to the newest served `gpt-<version>-<family>`, optionally suffixed `-low`, `-medium`, `-high` or `-xhigh`, SHALL run on that model; a Claude model name on the compatibility route SHALL run on the canonical Sol model; an unresolvable GPT name MUST be refused with HTTP 400 before any upstream call. Reasoning effort SHALL be the suffix effort when one is given, otherwise a supported per-request value (`low`, `medium`, `high`, `xhigh`) supplied by the Claude Code harness via `output_config.effort`, and MUST default to `high` when the value is missing or unsupported.

#### Scenario: Conflicting client controls

- **WHEN** Claude Code or its environment supplies a Claude model or a different service tier on the compatibility route
- **THEN** the server sends the canonical Sol model and `priority` to the Responses route

#### Scenario: New served release

- **WHEN** the served model list gains `gpt-7-sol` and a request names `sol-latest-low` or `gpt-7-sol-high`
- **THEN** the request runs on `gpt-7-sol` with the suffix effort, with no code change

#### Scenario: Unserved GPT name

- **WHEN** a compatibility request names a GPT model that is not served
- **THEN** the server returns HTTP 400 and makes no upstream call

#### Scenario: Per-task reasoning effort

- **WHEN** a compatibility request carries `output_config.effort` of `low`, `medium`, `high`, or `xhigh`
- **THEN** the translated Responses request and its accounting use that effort

#### Scenario: Unsupported effort value

- **WHEN** a compatibility request carries a missing or unsupported effort value
- **THEN** the translated Responses request uses `high`
