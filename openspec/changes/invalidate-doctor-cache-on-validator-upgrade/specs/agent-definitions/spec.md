## ADDED Requirements

### Requirement: Validator-aware cached verdicts
Agent definition verdict cache identity MUST include the executing validator implementation. Replacing the validator while preserving definition content, roots, catalog, and state MUST invalidate an obsolete verdict. Repeated invocations of an unchanged validator MUST continue to reuse valid cached verdicts. Invalidation MUST NOT delete operator state or alter definitions.

#### Scenario: Validator upgrade accepts a previously rejected alias
- **GIVEN** a cached rejection from an older validator
- **WHEN** the executable is replaced at the same path with a validator that accepts that alias
- **THEN** the next invocation reevaluates and reports the accepted definition
- **AND** an unchanged subsequent invocation uses the refreshed cache
