# Surface banked reset credits in status

## Why

The read-only status CLI omits the account-specific banked reset-credit count already present in `GET /api/accounts`. Its human output also shows Fable-scoped quota for an Opus assessment, making unrelated telemetry look relevant.

## What changes

- Project the nullable `resetCreditsAvailable` count from the existing accounts response as `reset_credits_available` in each status JSON account.
- Show banked resets in human output, distinguishing unknown from zero.
- Show Fable-scoped weekly telemetry in human output only for a Fable model assessment. Retain the existing JSON `fable` object for compatibility.
- Recognize the common `opus` and `sonnet` model aliases as Anthropic.

The command remains read-only and advisory. It does not list credits upstream or redeem them.
