# Refresh Kimi quota in the background

## Why
The Kimi quota reader is implemented but the periodic scheduler excludes Kimi. Account and pool views retain an old snapshot after real upstream quota changes.

## What Changes
Use the updater's supported-provider set for periodic usage refresh, without adding Kimi to inference warmups.

## Impact
Periodic quota reads and account/pool summaries; no credential or request-payload changes.
