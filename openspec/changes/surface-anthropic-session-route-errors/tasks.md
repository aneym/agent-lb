## 1. Implementation

- [x] 1.1 Update `/api/anthropic/session-route` to preserve Anthropic selection failure code and message.
- [x] 1.2 Add an integration test for the launcher preflight error envelope.
- [x] 1.3 Update the Claude launcher to print a concise account-status summary instead of raw JSON.

## 2. Verification

- [x] 2.1 Run the targeted Anthropic proxy test.
- [ ] 2.2 Validate the OpenSpec change.
- [x] 2.3 Restart the local agent-lb service and verify the live route returns the real failure message.
- [x] 2.4 Run the launcher formatting unit test.
