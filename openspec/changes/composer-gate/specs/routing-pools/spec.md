## ADDED Requirements

### Requirement: Composer rungs hold while Composer is out of usage
The interim composer rungs for implement, mechanical and explore SHALL carry a closed gate whose note names the evidence and the reopen condition (the Cursor cycle reset or a passing probe). A gated composer rung SHALL be skipped with a reason that starts with "gated:".

#### Scenario: Devin leads while Cursor is gated and Codex runs low
- **GIVEN** the canonical table, an eligible Cursor models pool, an eligible Devin pool, and Codex eligible and running low
- **WHEN** implement, mechanical and explore picks run
- **THEN** they select swe2-high, swe2-medium and swe2-medium respectively, list composer as gated, and list their Sol rung as running low

#### Scenario: Reopening the gate restores Composer
- **GIVEN** a copy of the canonical table with the Cursor rung gates removed
- **WHEN** a mechanical pick runs
- **THEN** it selects composer on cursor-seat
