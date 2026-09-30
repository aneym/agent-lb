## Why
Open Factory picks seats but callers still need vendor-specific launch and fallback logic. A single installable command should execute a brief and leave receipts.

## What Changes
- Add `of run` and `open-factory run` with host routing, intended-model selection, vendor adapters and up to three attempts.
- Record decisions, attempt outcomes and output paths.
- Add the `of` alias, symlink installer, usage README and doctor checks for execution dependencies and stand-ins.

## Capabilities
### New Capabilities
- `open-factory-run`: execute a routed job with bounded stand-ins and receipts.

### Modified Capabilities
None.

## Impact
Stdlib-only Open Factory CLI; no service, routing policy or credential changes. Box execution and agent-definition procedures remain out of scope.
