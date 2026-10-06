## Why
The shared endpoint parser validates after shell word splitting, allowing whitespace in a hostname to place URL content in endpoint logs. The real-probe shell fixture also requires a log line that is intentionally absent on immediate readiness.

## What Changes
- Validate the complete parsed hostname and numeric port before formatting endpoint output.
- Cover malformed endpoints at the shell subprocess boundary.
- Require endpoint logging only when the real-probe fixture waits; always reject credential leakage.

## Impact
Shared boot/watchdog endpoint parsing and focused regression fixtures only. No deployment or runtime configuration changes.
