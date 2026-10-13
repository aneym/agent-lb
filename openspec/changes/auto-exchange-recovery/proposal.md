# Recover eligible uncertain exchanges automatically

## Why
An unresolved refresh can remain exchange_uncertain even after its processing
window closes: ensure_fresh returns an unexpired access token without consulting
the intent, and usage refresh freshness/cooldowns or reset priority can skip it.
An already ambiguous replay or unsaved success still requires operator judgment.

## What Changes
- Consult the database-clock intent window during normal auth freshness checks.
- Use the existing usage scheduler to revisit uncertain accounts before usage
  freshness, cooldown and near-reset priority gates.
- Keep one automatic replay per unresolved token generation, ownership and
  token fences, and permanent-status admission; never clear an intent to recover.

## Impact
No schema, new scheduler, or provider contract change. Regression proof
uses real database persistence and a loopback OAuth server, without mocking our code.
