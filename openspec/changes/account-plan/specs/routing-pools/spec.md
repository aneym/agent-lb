## ADDED Requirements

### Requirement: Account plan recommendations
The pools API SHALL expose `/api/pools/plan` with generatedAt, recommended balanced, three levels, account deltas, installed ladders and E16 costs. Claude SHALL never be dropped. Budget SHALL drop two OpenAI accounts only for a non-OpenAI implementation head and all Devin accounts only when Devin is slowest. Balanced SHALL keep accounts, adding a Cursor plan when models usage exceeds 60 percent before cycle day 20. Unlimited SHALL add weekly-empty Claude accounts, one OpenAI account below 25 percent remaining and one Cursor plan for a Cursor implementation head. Unlimited review SHALL use Opus and cross-vendor Sol review for Claude work.

#### Scenario: Recommendations follow live pools
- **WHEN** weekly-empty Claude accounts, low OpenAI capacity and a Cursor implementation head are observed
- **THEN** Unlimited recommends the corresponding additions while Budget preserves Claude and removes entirely dropped pools from its ladders

### Requirement: Plan CLI
The routing CLI SHALL read the plan API and print the recommended level by default, support an explicit level and emit the complete API response as JSON.

#### Scenario: Explicit level
- **WHEN** `route plan --level budget` is invoked
- **THEN** it prints Budget accounts, reasons, ladders and risk

### Requirement: Weekly refill cohorts
Weekly pools SHALL expose future reset cohorts grouped within one hour, excluding canceled subscriptions, with at, accounts and remainingPercent. Pool objects SHALL expose totalAccounts and usableAccounts. Menu text SHALL show weekly refills with explicit account units.

#### Scenario: Three accounts refill together
- **WHEN** three accounts reset within an hour
- **THEN** menu text includes `+3 accounts` for that cohort
