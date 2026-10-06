## ADDED Requirements

### Requirement: Readmitted retired families
The routing table MAY name, under `readmitted`, a seat with one alias and the retired patterns it may use. The router SHALL resolve that alias while ignoring only those patterns, and SHALL keep treating a bare id that matches them as retired. The seat guard SHALL admit a dispatch that uses those patterns only when its subagent type is the named seat.

#### Scenario: Readmitted alias resolves
- **WHEN** `route resolve fable-latest` runs and the upstream Anthropic list includes `claude-fable-5-1`
- **THEN** it prints `claude-fable-5-1`

#### Scenario: Bare retired id stays retired
- **WHEN** `route resolve claude-fable-5-1` runs
- **THEN** it exits 2 and stderr says the id is retired

#### Scenario: Only the named seat is admitted
- **WHEN** an Agent dispatch pins `gpt-6-astra` on `astra-consult`
- **THEN** the seat guard admits it
- **WHEN** the same pin is on `sol-consult`
- **THEN** the seat guard denies it

### Requirement: Astra bridge alias
The ccgpt bridge SHALL resolve `astra-latest`, optionally suffixed with a reasoning effort, to the newest served `gpt-<version>-astra`.

#### Scenario: Astra alias with effort
- **WHEN** a Messages request names `astra-latest-high` and the LB serves `gpt-6-astra`
- **THEN** the bridge runs `gpt-6-astra` at high effort
