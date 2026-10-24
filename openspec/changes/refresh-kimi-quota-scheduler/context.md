# Incident evidence

October 23, 2026: the account row retained a 00:34 UTC snapshot with zero 5h usage while the configured coding key upstream reported used=100/100 and HTTP 429. Periodic scheduler filtering omitted Kimi despite its implemented reader and updater support. The browser console independently matched monthly 6.11% usage, 100% rate-limit usage, and the same request history.

The fix enrolls all providers supported by the usage updater. Inference warmup enrollment remains unchanged. A scoped lb-restart exercised the scheduler against the real upstream and the account/pool HTTP responses then reflected zero remaining and the 05:34:28 UTC reset without manually changing quota state.

Check: `.venv/bin/python scripts/check_kimi_quota.py --remaining 0 --reset 2026-10-24T05:34:28Z`. This real-HTTP check failed against the stale pre-fix account response, then passed after deployment. Evidence is retained outside git at `/Users/aneyman/.agent-rails/lanes/kimi-quota-recovery/evidence`. No inference success is claimed while upstream rejects the configured coding key.
