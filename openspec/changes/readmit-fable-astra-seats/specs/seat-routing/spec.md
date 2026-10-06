## ADDED Requirements

### Requirement: Readmitted retired families
The routing table MAY name, under `readmitted`, a seat with one alias and the retired patterns it may use. The router SHALL resolve that alias while ignoring only those patterns, and SHALL keep treating a bare id that matches them as retired. A routed seat other than the named one SHALL NOT reach the alias. The named seat SHALL use the family without an explicit request.

#### Scenario: Readmitted alias resolves
- **WHEN** `route resolve fable-latest` runs and the upstream Anthropic list includes `claude-fable-5-1`
- **THEN** it prints `claude-fable-5-1`

#### Scenario: Bare retired id stays retired
- **WHEN** `route resolve claude-fable-5-1` runs
- **THEN** it exits 2 and stderr says the id is retired

#### Scenario: Another seat's ladder rung names the alias
- **WHEN** a `gpt-explorer` rung names `astra-latest`
- **THEN** `route pick` skips it

### Requirement: Retired models run on explicit request
A model matching `retired` SHALL NOT be reached by an alias, pick, ladder rung or fallback, and the seat guard SHALL deny a subagent type whose definition pins one when the dispatch and brief do not name it. A dispatch `model`, a brief pin, a literal Workflow `opts.model` or `route resolve <id>` that names one SHALL be admitted and recorded in the routing ledger with its source and the dispatch description. A model matching `blocked` SHALL be denied everywhere.

#### Scenario: Named on the dispatch
- **WHEN** an Agent dispatch on `opus-seat` sets model `claude-fable-5-1`
- **THEN** it runs and the ledger record carries `explicit_models` with source `dispatch`

#### Scenario: Silent definition default
- **WHEN** a subagent type is defined on `claude-fable-5-1` and the dispatch names no model
- **THEN** the seat guard denies it

#### Scenario: Blocked model
- **WHEN** a dispatch names `claude-planner`
- **THEN** the seat guard denies it as no longer served

### Requirement: Astra bridge alias
The ccgpt bridge SHALL resolve `astra-latest`, optionally suffixed with a reasoning effort, to the newest served `gpt-<version>-astra`.

#### Scenario: Astra alias with effort
- **WHEN** a Messages request names `astra-latest-high` and the LB serves `gpt-6-astra`
- **THEN** the bridge runs `gpt-6-astra` at high effort
