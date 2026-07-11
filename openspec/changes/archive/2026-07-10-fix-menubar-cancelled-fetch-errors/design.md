## Context

The open-popover polling task owns child fetches. Closing the popover cancels
that task and its children. The API layer exposes URL cancellation as a
transport error, and each section fetch currently records every caught error as
a visible failure. Silent closed-state fetches retain stale errors even after a
successful refresh.

## Goals / Non-Goals

**Goals:**

- Make a section error mean that the most recent completed fetch failed.
- Keep cancellation from creating or clearing visible error state.
- Let any successful fetch clear an older visible error.

**Non-Goals:**

- Redesign retry rows or panel sizing.
- Refactor API client injection or polling ownership.
- Hide genuine failures when stale data remains available.

## Decisions

### Classify cancellation at the AppState boundary

Use one testable helper that recognizes `CancellationError`, direct cancelled
`URLError`, and the API transport wrapper around a cancelled `URLError`.
Foreground fetch catch blocks return without changing section state for those
errors. This keeps cancellation policy beside the section-state mutation it
governs.

### Silent success repairs stale errors

Silent fetch failures remain non-alerting and do not add errors. A silent fetch
that succeeds removes the corresponding existing error because its fresh data
disproves that failure state.

## Risks / Trade-offs

- Cancellation wrapped in a future error type could escape classification. The
  focused helper tests pin all forms currently emitted by this client.
- Full AppState networking is not injected. A narrow classifier and state
  transition helper test the production mutation path without a broad
  unrelated refactor.
