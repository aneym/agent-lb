# Tasks

- [x] Detect fresh unstarted OpenAI quota windows on every usage refresh, independent of prior exhaustion and working hours.
- [x] Gate on global and account warm-up opt-in, safe account state, and fresh non-exhausted telemetry.
- [x] Persist hourly continuous attempts, retry explicit failures with backoff and a failure brake, and keep pending attempts unretired.
- [x] Persist sent outcomes before request logging, log primer attempt/result safely, and expose successful primers in last-primed status.
- [x] Add service and repository regression coverage and validate OpenSpec.
- [x] Gate the continuous OpenAI primer on local ownership and cover mirrored and locally owned accounts.
- [x] Sanitize primer error codes in logs while retaining persisted outcomes; cover untrusted and structured codes.
- [x] Space continuous primers after any non-failed warm-up attempt and prove pending-row dedup beyond spacing.
- [ ] Exercise the live reset path, then merge and restart.
