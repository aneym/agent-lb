# Advisory account status CLI

## Why
An interactive Fable conversation was denied despite remaining scoped quota because
the deployed runtime had a 90-percent cutoff. A separate seat guard denied launches
from reserve estimates. The owner requested visibility through a CLI instead of
these artificial admission gates.

## Changes
- Add read-only `agent-lb status` with human and JSON output and provider/model filters.
- Report real windows, freshness, resets, and current routing-policy observations.
- Make capacity-related seat-guard results advisory; preserve unrelated controls.
- Restore the deployed Fable threshold to the source default of 100 percent.

## Compatibility
No-argument server startup and existing CLI commands remain unchanged. No new
provider calls, spend, or account mutations. Actual provider limits and auth remain.
Support-intake templates, account-profile configuration, and security policy are
unaffected. Public-release prose tests are unchanged: CLI behavior is covered by
focused executable tests, not assertions on literal documentation.
