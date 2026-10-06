## ADDED Requirements

### Requirement: Picks skip seats with no lease capacity
A route pick SHALL consult live provider reservations, skip seats whose capacity is fully reserved, and log the reason. A pick SHALL NOT itself acquire or release a reservation.

#### Scenario: Devin has a live reservation
- **WHEN** Devin is healthy but all its configured reservation capacity is held
- **THEN** pick skips Devin, records the reason and chooses the next available audited seat

#### Scenario: Capacity is released
- **WHEN** the Devin reservation is released
- **THEN** the next pick can name Devin again

#### Scenario: Reservation service is unavailable
- **WHEN** a pick cannot read live reservations
- **THEN** it reports availability as unknown and leaves atomic admission to reserve
