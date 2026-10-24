## ADDED Requirements

### Requirement: Periodic Kimi quota refresh
The service SHALL periodically refresh Kimi accounts using the measured coding quota endpoint, under the same freshness and authentication rules as the usage updater.

#### Scenario: Kimi upstream usage changes
- **WHEN** a Kimi quota snapshot becomes stale and the usage scheduler runs
- **THEN** the next account and pool responses SHALL reflect the refreshed quota and reset time
- **AND** Kimi SHALL NOT be enrolled in inference warmups merely by enabling quota refresh
