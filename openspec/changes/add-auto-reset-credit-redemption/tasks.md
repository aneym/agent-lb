# Tasks

- [x] 1. Settings: `reset_credit_auto_redeem_enabled` / `_interval_seconds` / `_cooldown_seconds`
- [x] 2. `ResetCreditAutoRedeemScheduler` with leader election, pool-exhaustion guard, candidate ordering (quota_exceeded first, earliest-expiring credit), cooldown, audit logging
- [x] 3. Wire scheduler start/stop into `main.py` lifespan
- [x] 4. Unit tests: candidate selection ordering, active-account suppression, cooldown, exclusion of non-serving accounts, failure fall-through, kill switch
- [ ] 5. `ruff` + tests green; deployed to both instances; guard behavior verified live (active accounts present → no redemption)
