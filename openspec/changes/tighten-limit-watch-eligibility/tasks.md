# Tasks

## 1. Eligibility and snapshot contract

- [x] 1.1 Implement fail-closed core-window, status, and future rate-limit eligibility with the documented OpenAI null-primary exception; verify the focused stdlib suite passes under `/usr/bin/python3`.
- [x] 1.2 Require Fable scoped-weekly headroom and apply freshness only from `fableScopedWeekly`; emit provider minimum and redacted unusable-account diagnostics.

## 2. Validation

- [x] 2.1 Keep no more than nine fixture-shaped regression cases, including the days-old OpenAI auth-refresh regression; capture its failure against `835e16f5` and verify all cases pass under `/usr/bin/python3`.
- [x] 2.2 Validate the OpenSpec change and repository static checks; record commands and results.
