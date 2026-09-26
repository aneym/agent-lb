## Decision

Inventory is live but banked credit quantity is not a provider eligibility guarantee. `redeemableNow` describes current provider eligibility when the server can establish it; omitted or null values mean unknown, not yes. The CLI never sends the API's `overrideDailyLimit` option.

The local service remains the authority for whether an attempted redemption is applied. A post-confirmation `not_redeemed` response is surfaced with its code and a nonzero exit; the CLI does not try again because a repeated consume call may have an uncertain outcome.

## Example

`agent-lb resets list --account user@example.test` may show `Banked reset credits: 1` and `Currently redeemable: no (provider_not_eligible)`. In that state, `resets redeem` stops before POST. An older server without eligibility fields displays `unknown (banked does not mean redeemable)` and allows one explicitly confirmed attempt.
