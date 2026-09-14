# Recover rejected Codex continuation anchors

## Why

Two Codex lanes terminated on the ChatGPT websocket error `Invalid previous_response_id`. The backend actually wraps the parameter in backticks and omits `code` and `param`. The classifier recognized only `previous_response_not_found` and a different invalid-request shape. Existing affinity selected the same account as preceding successful requests in both incidents.

## What changes

- Recognize the exact backend error and normalize its error type when code is absent.
- Use existing bounded full-history recovery for rejected anchors. Short continuations retain an explicit continuity error.
- Allow a pre-created quota rejection to rotate only a verified full-history websocket request, after removing the old anchor. Preserve file pins, already-started requests, request reservations and the one-replay bound.
- Retain session/turn-state affinity and persisted previous-response owner lookup.

## Impact

Three proxy implementation files, focused unit/integration tests, and sticky-session requirements. No migration, credential, model, pricing or frontend changes.
