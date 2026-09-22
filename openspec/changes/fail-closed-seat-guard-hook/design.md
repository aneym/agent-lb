# Design

## Context

The hook imports `json` before its broad exception handler, and its installed shell command treats hook failures as successful. The implementation needs a response path that still works when Python module resolution is compromised.

## Goals / Non-Goals

**Goals:**
- Emit a Claude hook denial if the hook process fails before normal handling.
- Keep the emergency output independent of the `json` module.
- Preserve unrelated hook registrations and the load-governor wrapper.

**Non-Goals:**
- Change normal seat-selection decisions or the load-governor failure policy.
- Recover from failures that prevent the Python interpreter or shell itself from starting.

## Decisions

- The generated command will redirect hook stderr and use a shell `||` branch that prints a static denial JSON document. This protects failures before Python can write a valid hook response; `|| true` does not.
- The guard will retain JSON serialization for normal decisions but import it within the protected block. Its exception fallback will print a literal JSON document, avoiding a dependency on the failing import. A manually escaped constant is preferred to a serializer because the fallback must not require additional imports.
- Regression tests will execute the emitted command in `/bin/sh` and shadow `json` via `PYTHONPATH`, testing observable standard output rather than implementation details.

## Risks / Trade-offs

- [The shell fallback has fixed wording] → Use the canonical crash reason and only activate it after a non-zero process result.
- [Hand-built JSON can be malformed] → Keep it a static literal and test that the emitted document parses as a denial.

## Migration Plan

The installer converges existing policy settings to the new managed hook command. Uninstall continues to remove only routing-owned hook registrations.
