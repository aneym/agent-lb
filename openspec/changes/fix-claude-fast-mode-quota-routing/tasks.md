## 1. Implementation

- [x] 1.1 Add a dedicated Anthropic fast-mode quota definition.
- [x] 1.2 Route `speed: "fast"` requests through the fast quota while keeping sticky session keys model/effort scoped.
- [x] 1.3 Parse Anthropic fast-mode reset headers for cooldown retry metadata.
- [x] 1.4 Allow session-route callers to preflight the fast quota when explicitly requested.
- [x] 1.5 Ensure Anthropic OAuth and fast-mode beta headers are present on the matching upstream requests.

## 2. Verification

- [x] 2.1 Add regression tests covering fast cooldown isolation and standard fallback.
- [x] 2.2 Run targeted Anthropic proxy and settings tests.
- [x] 2.3 Validate the OpenSpec change.
- [x] 2.4 Verify the local service with curl or the Claude TUI path.
