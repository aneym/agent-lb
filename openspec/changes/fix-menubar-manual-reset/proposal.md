# Fix menubar manual resets

## Why
Reset requests lose server errors and fail silently at automatic policy caps.

## What Changes
Confirm credit spend; provide persistent reset status and explicit Claude policy override without changing automatic allowance.

## Impact
Account consume API and native Mac menubar.
