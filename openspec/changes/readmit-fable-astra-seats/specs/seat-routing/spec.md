## ADDED Requirements

### Requirement: Readmitted retired families
The routing table MAY name, under `readmitted`, a seat with one alias and the retired patterns it may use. The router SHALL resolve that alias while ignoring only those patterns; a bare id that matches them stays retired and resolves only as an explicit request. An alias MAY name a `fallback` alias the router uses when the family is not served. A routed seat other than the named one SHALL NOT reach the alias. The named seat SHALL use the family without an explicit request.

#### Scenario: Readmitted alias resolves
- **WHEN** `route resolve fable-latest` runs and the upstream Anthropic list includes `claude-fable-5-1`
- **THEN** it prints `claude-fable-5-1`

#### Scenario: Readmitted alias falls back
- **WHEN** `route resolve fable-latest` runs and the upstream Anthropic list lacks every Fable model
- **THEN** it prints the `opus-latest` model

#### Scenario: Bare retired id is an explicit request
- **WHEN** `route resolve claude-fable-5-1` runs
- **THEN** it prints `claude-fable-5-1` and the routing ledger records an `explicit_model` event

#### Scenario: Another seat's ladder rung names the alias
- **WHEN** a `gpt-explorer` rung names `astra-latest`
- **THEN** `route pick` skips it

### Requirement: Retired models run on explicit request
A model matching `retired` SHALL NOT be reached by an alias, pick, ladder rung or fallback, and the seat guard SHALL deny a subagent type whose definition pins one when the dispatch and brief do not name it. A dispatch `model`, a brief pin, a literal Workflow `opts.model` or `route resolve <id>` that names one SHALL be admitted and recorded in the routing ledger with its source and the dispatch description.

#### Scenario: Named on the dispatch
- **WHEN** an Agent dispatch on `opus-seat` sets model `claude-fable-5-1`
- **THEN** it runs and the ledger record carries `explicit_models` with source `dispatch`

#### Scenario: Silent definition default
- **WHEN** a subagent type is defined on `claude-fable-5-1` and the dispatch names no model
- **THEN** the seat guard denies it

#### Scenario: Silent definition default in a Workflow
- **WHEN** a Workflow `agent()` names a seat defined on `claude-fable-5-1` and no model
- **THEN** the workflow seat guard denies it

### Requirement: Blocked models
An exact pin (dispatch `model`, seat definition, Workflow literal or args) matching `blocked` SHALL be denied. A brief's prose matching `blocked` SHALL be admitted with an advisory and logged, because a regex over prose cannot tell a request from a mention.

#### Scenario: Blocked model
- **WHEN** a dispatch names `claude-planner`
- **THEN** the seat guard denies it as no longer served

#### Scenario: Blocked model in prose
- **WHEN** a brief says `--model gpt-5.4-mini`
- **THEN** the dispatch runs, the guard returns an advisory, and the ledger records the model as blocked

### Requirement: Caller choice over the ladder default
The first open ladder rung SHALL stay the default. `route pick` SHALL accept `--prefer <rung or seat>` and `--effort <level>` with a required `--reason`, honor them, and append a `route_override` event with the default rung and reason to the routing ledger. A preference SHALL waive the table's policy holds on that rung (gate, `when`, reserve, stand-in only) and SHALL NOT waive an unresolvable model, an exhausted pool, a down seat, same-maker review or a missing reviewer.

#### Scenario: Preferred rung
- **WHEN** `route pick explore --prefer swe2-medium --reason x` runs while `sol-low` is open
- **THEN** it picks `swe2-medium`, reports `default: sol-low` and logs the override

#### Scenario: Preference without a reason
- **WHEN** `route pick explore --prefer swe2-medium` runs
- **THEN** it exits 3 with `missing_reason`

### Requirement: Explore needs no auditor
The explore class is read-only and SHALL pick without an auditor.

#### Scenario: Explore with every reviewer closed
- **WHEN** every verify pool is exhausted
- **THEN** `route pick explore` still picks a rung, with `audit: null`

### Requirement: Astra bridge alias
The ccgpt bridge SHALL resolve `astra-latest`, optionally suffixed with a reasoning effort, to the newest served `gpt-<version>-astra`.

#### Scenario: Astra alias with effort
- **WHEN** a Messages request names `astra-latest-high` and the LB serves `gpt-6-astra`
- **THEN** the bridge runs `gpt-6-astra` at high effort
