# Repair team-mode verification prerequisites

## Why

The team-mode contract requires the complete Python suite and application format check to pass. The September 19 run passed every Team test but exposed 32 existing failures and 11 format violations. The operator authorized self-healing needed to complete the goal.

## What changes

- Repair migration compatibility with already-created reset-credit tables and equivalent SQLite index reflection.
- Invalidate cached account quota responses after routing-policy edits.
- Bring stale test fixtures into agreement with current provider catalogs, mapper arguments, selection persistence signatures, and timing contracts without removing assertions.
- Scope the June release-candidate task snapshot test to that release's changes; retain its exact PR-evidence checks.
- Supply missing normative spec deltas for three existing documented runtime fixes.
- Format the application files flagged by the required check.

## Impact

These prerequisite repairs remain in the isolated team-mode worktree. They are not deployed with the operational Team feature and do not modify unrelated dirty work in main. No existing migration revision is edited and no new schema change is intended.
