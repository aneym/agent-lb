## ADDED Requirements

### Requirement: Measured Kimi Coding usage
The service SHALL fetch Kimi Coding usage using the provider's official quota contract and persist valid window utilization and reset timestamps. Missing or malformed telemetry SHALL NOT create full-capacity readings or erase previously known valid windows. Usage-fetch errors SHALL NOT infer an authentication ban.

#### Scenario: Exhausted short window
- WHEN Kimi usage reports a fully used five-hour window and an unexhausted weekly window
- THEN account and pool views SHALL report the measured remaining values and provider resets

#### Scenario: Missing usage
- WHEN Kimi usage is unavailable and no measured window exists
- THEN dashboard and native menubar SHALL display unknown quota rather than a synthesized 100 percent

### Requirement: Native measured quota
The native menubar SHALL show Kimi measured quota windows and reset times from the API without moving Kimi out of Other or restoring a Muse tab.

#### Scenario: Fresh Kimi quota telemetry
- WHEN the next native account poll receives measured Kimi quota
- THEN it SHALL display those measured remaining percentages and resets
