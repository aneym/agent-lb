## ADDED Requirements

### Requirement: Reset commands select one exact account

The CLI MUST resolve `--account` against an exact account ID or case-insensitive exact email from `GET /api/accounts`. It MUST reject zero or multiple matches before requesting inventory or redemption.

#### Scenario: Duplicate email
- **WHEN** two accounts have the requested email
- **THEN** the CLI exits nonzero without requesting inventory or redeeming a credit

### Requirement: Listing separates banked inventory from eligibility

The CLI MUST fetch live inventory from `GET /api/accounts/{id}/rate-limit-reset-credits` and display the banked available count. It MUST display `redeemableNow` and `ineligibleReason` when provided, and MUST report eligibility as unknown when omitted; it MUST NOT infer eligibility from the count alone.

For Claude accounts, the GET inventory response MUST expose optional `redeemableNow` and `ineligibleReason` fields derived from current provider eligibility. A banked count MUST NOT be presented as proof of immediate redeemability.

When a Claude reset grant has `use_requires_limit: false`, the service MUST permit redemption without local limit-exhaustion evidence if the provider marks the account and selected grant eligible and usable now. Grants requiring a limit MUST still require recoverable exhaustion. Provider cooldowns and grant timing MUST remain enforced.

#### Scenario: Older server omits eligibility
- **WHEN** inventory has a positive available count but no eligibility fields
- **THEN** listing displays a banked credit and unknown current redeemability

#### Scenario: Provider-authorized early reset
- **GIVEN** an eligible Claude account whose selected usable grant does not require a limit
- **WHEN** the account is not at its usage limit and an operator redeems the grant
- **THEN** the service does not reject it as `not_eligible` solely because no limit is exhausted

### Requirement: Redemption is deliberate and single-attempt

The CLI MUST send at most one POST to `/api/accounts/{id}/rate-limit-reset-credits/consume`, with optional `creditId`. It MUST send `overrideDailyLimit` only for a Claude account when the operator explicitly supplies `--override-daily-limit`; the flag bypasses Agent LB's local daily cap but cannot bypass provider eligibility. It MUST require `--yes` or an affirmative interactive confirmation before POST. It MUST exit nonzero without POST when inventory shows no available credits or a known ineligible state. It MUST display response `status` and `code`, and exit nonzero for any status other than `redeemed`.

#### Scenario: Declined confirmation
- **WHEN** the operator declines or stdin is noninteractive without `--yes`
- **THEN** no redemption POST occurs

#### Scenario: Provider returns no-op
- **WHEN** a confirmed POST returns `status: not_redeemed`
- **THEN** the CLI displays the code and exits nonzero without retry
