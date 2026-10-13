## Why
Live policy runs fetch reset inventory before checking quota and fail on upstream 429 responses. Ten-second status samples cannot measure burn at whole-percent precision.

## What Changes
- Fetch inventory only after every live Claude account is below 5%.
- Hold with exit 0 when inventory is unavailable.
- Persist window samples for 48 hours and measure burn against the newest matching sample at least five minutes old.

## Impact
The reset policy CLI gains `--samples PATH`. Redemption continues through the existing consume endpoint.
