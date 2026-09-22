## ADDED Requirements

### Requirement: Menu-bar reset display distinguishes a full window from a pending recovery

The macOS menu-bar client MUST render `Full · no reset needed` instead of a
reset countdown when a displayed pool window is known full. A window is known
full when positive-capacity credit telemetry has remaining credits greater than
or equal to capacity, or, when those credits are unavailable, reported
remaining percent is at least 100. It MUST NOT infer full from a rounded
percentage, zero capacity, or missing telemetry.

#### Scenario: Full five-hour pool does not promise zero recovery

- **GIVEN** a five-hour window reports `500 / 500` credits and a future reset timestamp
- **WHEN** the menu-bar pool card renders
- **THEN** it shows `Full · no reset needed`
- **AND** it does not show a countdown or `+0 cr`

#### Scenario: Scoped recovery ignores a full earlier account

- **GIVEN** a full account resets before a depleted account in the same scoped pool
- **WHEN** the menu-bar computes the next recovery and its hover schedule
- **THEN** the depleted account determines the next recovery
- **AND** the full account is omitted from that schedule

#### Scenario: Unknown telemetry remains unknown

- **GIVEN** an account has a future reset timestamp but missing credit and percent telemetry
- **WHEN** the menu-bar computes a scoped pool
- **THEN** it does not infer that the pool or account is full
- **AND** the account can still determine the displayed unknown reset boundary

#### Scenario: Fractional recovery is not displayed as zero

- **WHEN** a known positive recovery is less than one credit
- **THEN** the menu-bar renders `+<1 cr` rather than `+0 cr`

#### Scenario: Full account row omits the countdown

- **GIVEN** an account has a known-full five-hour window and a depleted weekly window
- **WHEN** its row renders
- **THEN** the five-hour window displays its remaining percent without a countdown
- **AND** the weekly window retains its own reset countdown
