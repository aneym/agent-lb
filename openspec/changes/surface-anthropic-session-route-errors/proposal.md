# surface-anthropic-session-route-errors

## Why
Claude Code launchers call `/api/anthropic/session-route` before starting so they can pin a Claude session to an Anthropic account. When all Anthropic accounts are unavailable, the route currently raises an `HTTPException` with structured detail, but the shared OpenAI error handler replaces that detail with the generic message `Request failed`.

That hides the operator-actionable reason, such as all Claude accounts being rate-limited or no Anthropic account being selectable.

## What Changes
- Return an OpenAI-compatible error envelope directly from `/api/anthropic/session-route` when Anthropic account selection fails.
- Preserve the Anthropic selection failure code and message in the response body.
- Have the Claude launcher unwrap that envelope and print a concise human-readable status summary.
- Add regression coverage for the no-selectable-Anthropic-account launcher preflight path.

## Impact
- Claude Code launcher errors explain the actual account-selection failure instead of showing `Request failed` or raw JSON.
- Existing successful session-route responses remain unchanged.
- The broader global HTTP exception behavior is left untouched.
