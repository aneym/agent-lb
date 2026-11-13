## ADDED Requirements

### Requirement: Explicit manual weekly override
The admin consume endpoint MUST accept `overrideWeeklyLimit`, default false. Only manual Claude resets MAY bypass the local weekly cap with this flag. This MUST NOT alter the automatic weekly allowance, ownership, subscription, 95% weekly usage threshold, usable grant, serialization or unknown-outcome recovery guards. The existing daily override remains separate. Audit details MUST record both flags and attempts with either flag MUST record manual_override.

#### Scenario: Ordinary reset at weekly cap
- **WHEN** a consume request omits the weekly override at the cap
- **THEN** it MUST fail without consuming a credit

#### Scenario: Confirmed manual reset
- **WHEN** the operator confirms the menubar Claude override
- **THEN** it MUST send both override flags once and display the result

### Requirement: Durable menubar feedback
The menubar MUST confirm account and credit spend before submitting, disable duplicate submissions while pending, and display loading, server rejection reason, non-redemption code or success until dismissed. Timeouts MUST advise reconciliation rather than automatic retry. Cancel MUST send no consume request.

#### Scenario: Server rejection
- **WHEN** the reset endpoint rejects a reset
- **THEN** the menubar MUST display its structured error message and not just a transient failure glyph

### Requirement: Per-grant scope
The inventory MUST expose each Claude grant's own clears, remaining count, expiry and provider eligibility/blocker. The menubar MUST NOT infer any grant's scope from the aggregate union. Unknown scope MUST remain unknown.

#### Scenario: Mixed grants
- **WHEN** an account holds account-wide and Fable-only grants
- **THEN** each grant MUST show its own count, scope, eligibility and expiry and submit its own creditId

### Requirement: Precise window labels
The menubar MUST label five_hour as the 5-hour window, seven_day as all-models weekly and seven_day_overage_included as Fable weekly. It MUST show separate explicitly used weekly readings and reported cleared windows and observed before/after readings without promising full all-models reclamation.

#### Scenario: Scoped redemption
- **WHEN** a Fable-only credit is reviewed
- **THEN** confirmation MUST say it does not reset the full all-models week

### Requirement: Reset recovery cooldown
Verified recovered capacity MUST clear obsolete account cooldown state using a guarded state update. This MUST NOT reactivate paused or disconnected accounts or overwrite concurrent state changes.

#### Scenario: Recovery
- **WHEN** fresh post-redemption capacity is verified
- **THEN** stale runtime cooldown MUST be cleared without overriding a concurrent account state transition
