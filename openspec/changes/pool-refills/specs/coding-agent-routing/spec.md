## ADDED Requirements

### Requirement: Weekly inspection shows account refill cohorts

Route pools and menu SHALL exclude canceled subscriptions from refill arithmetic and SHALL attach refills from available per-account weekly reset data, grouping resets within one hour and sorting cohorts by UTC time. Each cohort SHALL expose its reset time, account count and summed remaining percentage. Pace SHALL expose next_refill_h for the next reset of an account below ten percent remaining. Text output SHALL show a refill line per weekly pool with available account data. Unavailable account data SHALL omit refills without failing inspection.

#### Scenario: Spent Codex accounts refill in two cohorts

- **WHEN** three nearly spent accounts reset around 17:00 UTC, another resets at 21:06 UTC, and a 76-percent account resets later
- **THEN** refills groups the first three accounts and reports the two subsequent reset cohorts separately
- **AND** next_refill_h points to the first nearly spent account reset


#### Scenario: Canceled Codex subscription resets earlier

- **WHEN** a canceled quota-exceeded account resets in three hours before the usable spent accounts
- **THEN** the canceled account is absent from refills and does not determine next_refill_h
