# Pool reset timer

`app.pool_reset_auto` redeems one banked Claude reset when the Anthropic pool is about to run dry. It runs as the systemd user timer `agent-lb-pool-reset-auto` on the primary, every 5 minutes, with no model request: the primary owns the accounts, holds their tokens and stores the limit readings.

## Rule (`app/pool_reset_rule.py`)

- When: the usable accounts' all-models points left sum to 8 or fewer, or under 30 minutes are left at the last hour's pace. Readings older than 15 minutes on a usable account stop it.
- Cap: two in the week of Monday 2026-11-09 (weeks start Monday 00:00 America/New_York), one in each later week (`app/core/claude_reset_week.py`). agent-lb's own Claude redeem path refuses past the cap for every caller, counted from `reset_credit_attempts`; the timer applies the same count first. No second within 60 minutes of the last one, and none while the last one is unconfirmed. The timer passes `overrideDailyLimit` only for the second of a two-reset week when the first is under 24 hours old.
- What a reset gives back: on 502ebd48 (2026-11-10 03:17Z) a reset cleared the Fable weekly window (39% to 0%) and all-models fell 100% to 81%. The pick is the owned account where a reset frees the most all-models points: its Fable weekly use times its own all-models-per-Fable ratio over the last 24 h, capped at the ratio measured on 502ebd48. Accounts that free under 10 points, hold no banked reset, or are teammate-owned are skipped. Teammate-owned accounts (pushed by a teammate's agent-lb, today Nate's 25600cac, 86fe250c and bfb49472) are never redeemed: only their owner holds the sign-in, and the primary's redeem path refuses an account it does not own. A yes file `pool-reset-auto/teammate-yes/<short id>`, written only on Alex's or the teammate's word, is noted in the run log but still does not let the primary redeem it.

## Run

- `--live` acts; without it a run is a dry run that prints the decision. `--readings <headroom copy>` and `--redemptions <list>` decide on copies and never act.
- Each live redemption goes through the dashboard route `POST /api/accounts/{id}/rate-limit-reset-credits/consume` after a read of `GET .../rate-limit-reset-credits`, so it uses the tokens agent-lb already holds. If the call fails it tries the next candidate once, then writes a `hold` file and reports.
- After a redemption it waits for a lower reading in the account's weekly or Fable window before it sends one line to the Rails back office and Factory with `t3 thread send --mode queue` on ax102.

## State

`~/.agent-rails/factory/pool-reset-auto/` on the primary, mirrored to ax102: `runs.jsonl`, `last.json`, `pending.json` while a redemption awaits confirmation, and `receipts/`. A `hold` file in that folder on either host stops it.

## Install

`scripts/install-pool-reset-auto.sh` from the runtime checkout on the primary.
