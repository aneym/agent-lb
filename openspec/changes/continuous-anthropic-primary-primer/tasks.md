# Tasks

- [x] Extend existing Anthropic primer and refresh scheduler to evaluate known five-hour reset times.
- [x] Gate on active, opted-in, subscription-usable accounts and freshly observed free primary, weekly, and Haiku quota.
- [x] Persist one continuous attempt per account/reset, retry explicit failures with backoff, and log attempt/result.
- [x] Re-read usage after a successful send and expose last confirmed prime time in account status.
- [ ] Add regression coverage, validate OpenSpec, exercise the live reset path, then merge and restart.
