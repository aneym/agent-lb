## ADDED Requirements

### Requirement: Quota eligibility and diagnostics scope to the routable pool

The Anthropic model-quota eligibility prefilter and the no-account selection diagnostics MUST consider only the routable pool. The routable pool MUST exclude accounts that are reauth-required, deactivated, or paused, and accounts whose subscription is canceled. Stored accounts outside the routable pool MUST NOT be counted as blocked accounts, MUST NOT be passed to the selector as remaining candidates, and MUST NOT be included in the headline account total reported in the selection-failure message. When unusable stored accounts exist, the failure message MAY report their count in a separately labeled note.

#### Scenario: All routable accounts cooling down returns a native cooldown 429

- **GIVEN** every routable Anthropic account is cooling down on the requested quota key
- **AND** one or more stored accounts are unusable because the subscription is canceled, or the account is deactivated or paused
- **WHEN** a Claude request is routed for that quota key
- **THEN** the proxy responds with a native rate-limit `429` for the quota cooldown
- **AND** the response includes `Retry-After` and unified-reset metadata derived from the soonest routable reset
- **AND** the unusable stored accounts are not reported as remaining candidates

#### Scenario: Failure diagnostics count only routable accounts

- **GIVEN** a routable Anthropic account exists but is not selectable
- **AND** one or more stored accounts are unusable because the subscription is canceled, or the account is deactivated or paused
- **WHEN** account selection fails
- **THEN** the headline account total and status summary count only routable accounts
- **AND** the unusable stored accounts are reported in a separately labeled note
- **AND** the error preserves retry metadata and the provider-boundary message that OpenAI accounts cannot serve Claude routing
