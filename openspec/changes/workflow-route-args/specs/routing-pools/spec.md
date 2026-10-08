## ADDED Requirements

### Requirement: Workflow scripts receive routed agent options
`route workflow-args` SHALL print one JSON object with `seats` (one entry per non-verify class), `review_for` (one entry per author vendor), `agent_cap` and `errors`. Each entry SHALL carry the pick's seat, model and effort, `opts` for `agent()` and a `brief` line. Bridge seats (gpt-implementer, gpt-explorer, sol-consult) SHALL carry effort only in `opts.model` as `<alias>-<effort>`. Brief seats (cursor-seat, devin-seat) SHALL carry `opts.effort` low and `Seat model: <resolved id>` in the brief, adding `Seat mode: ask` for read-only rungs and, for reviewers, `Seat mode: ask` plus the verifier instructions. codex-sol SHALL carry `Effort: <effort>` in the brief. Fixed forwarders (codex-verifier, codex-test-runner, computer-use) SHALL carry only `agentType`. `review_for` SHALL include anthropic, openai, xai, cursor, cognition, devin, glm and kimi. Every other seat SHALL carry the resolved model and the effort in `opts`.

#### Scenario: Default pools
- **GIVEN** Cursor, Codex and Claude pools are eligible
- **WHEN** `route workflow-args` runs
- **THEN** implement is cursor-seat with brief `Seat model: grok-4.7-medium`, its auditor is verifier with model and effort in opts, explore is gpt-explorer with model `sol-latest-low`, and review for anthropic authors is codex-verifier

#### Scenario: Cursor models empty
- **GIVEN** cursor-models has no eligible account
- **WHEN** `route workflow-args` runs
- **THEN** implement is gpt-implementer with model `sol-latest-medium` and an empty brief

### Requirement: Workflow args are checked for retired models
The workflow seat guard SHALL deny a Workflow call whose `args` contain, in any object that also has an `agentType`, `seat` or `implementer` key, a `model` matching a retired pattern. A `model` in an object without such a key SHALL NOT be denied.

#### Scenario: Stale args
- **GIVEN** Workflow args with `opts: {agentType: 'opus-seat', model: 'claude-sonnet-5'}`
- **WHEN** the Workflow tool is called
- **THEN** the guard denies it and names the path of the retired model

#### Scenario: Baseline ladder with Claude on its last account
- **GIVEN** the baseline ladder and one eligible Claude account
- **WHEN** `route workflow-args` runs
- **THEN** the implement auditor is cursor-seat with brief `Seat model: claude-sonnet-5-5-high` and `Seat mode: ask`, and the codex-sol explore fallback has brief `Effort: medium`
