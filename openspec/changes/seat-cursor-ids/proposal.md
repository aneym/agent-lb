# Resolve Cursor Claude aliases before dispatch

## Why

Unknown family aliases and unsupported Cursor ids currently reach the CLI and fail without useful output.

## What Changes

- Resolve effort-qualified Sonnet and Opus aliases from Cursor's served list at standard speed.
- Reject undefined latest aliases with exit 3.
- Check Cursor model availability once per process before dispatch, preserving dispatch when discovery fails or is empty.
- Warn for superseded Claude generations without denying intentionally chosen, served models.

## Impact

- `clients/route`, `clients/seat`, and the canonical routing aliases.
