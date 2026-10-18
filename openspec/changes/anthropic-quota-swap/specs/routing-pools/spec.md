## ADDED Requirements
### Requirement: Quota authority swap
The router SHALL swap new authority jobs away from Anthropic below the existing reserve or after a limit error within ten minutes. It SHALL recover only above reserve plus ten percentage points with no error for fifteen minutes. It SHALL preserve first job arms and explicit model flags, log each flip, and expose mode, remaining fraction and last flip in `route quota-mode`.

#### Scenario: Quota falls and recovers
- **WHEN** Anthropic remaining falls below reserve
- **THEN** review jobs use Sol high and architecture jobs use Astra high
- **AND** recovery waits for the hysteresis threshold and error cooldown

#### Scenario: Existing job or explicit override
- **WHEN** a job already has an arm or the caller supplies explicit model flags
- **THEN** quota mode does not replace that choice
