# Cursor provider-native backup stays blocked

## Why

Open Factory needs a Cursor connection, catalog, and usage record, and a Grok disposition, without treating either as a live inference provider. Official Cursor login is a browser consent and cannot be finished headlessly. The owner does not believe a Grok subscription exists.

## What Changes

- Add a provider-native backup record for Cursor at `discovered` then `blocked`, with an empty unverified catalog, unknown entitlement, and provider-managed usage whose headroom is `NOT_EXPOSED`.
- Persist the one-step consent action `agent login`. This change does not run it, copy a browser session, or read credentials.
- Add a disabled Grok record that does not spend xAI API credit and is not enabled.
- Keep both records out of the live provider registry.

## Capabilities

### New Capabilities

- `provider-native-backup`: onboarding records for provider-native backups that are not live registry members.

## Impact

`app/core/providers/native_backup.py` and its unit tests. No proxy, OAuth, or account-import path changes.
