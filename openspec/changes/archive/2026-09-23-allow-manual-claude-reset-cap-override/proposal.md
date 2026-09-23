# Allow an explicit manual Claude reset cap override

## Why

Claude banked resets are limited to one applied redemption across the pool per rolling 24 hours. An operator sometimes needs to spend another eligible grant deliberately, but the current manual endpoint applies the same cap as unattended redemption and returns 409.

## What Changes

Add an optional `overrideDailyLimit` boolean to the existing consume request. It defaults to false. Only a manual Claude request may use it, and it bypasses only the local rolling-24h check. Provider grant eligibility, account authentication and subscription checks, the global active-attempt slot, and unknown-outcome reconciliation remain mandatory. Store overridden attempts with a distinct `manual_override` trigger and include the flag in the API audit details. OpenAI requests with the flag are rejected; OpenAI's redemption behavior otherwise stays unchanged. Automatic callers cannot acquire the override.
