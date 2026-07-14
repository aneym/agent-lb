## 1. Request Contract

- [x] 1.1 Add a typed Anthropic-defined tool variant and allow it alongside existing custom tools.
- [x] 1.2 Add model tests for both web-search versions, optional-field preservation, and existing custom-tool validation.

## 2. External Path Regression

- [x] 2.1 Add `/v1/messages` regression coverage proving a WebSearch payload reaches the Anthropic proxy service unchanged.
- [x] 2.2 Confirm server-tool stream bodies remain transparent to downstream clients.

## 3. Validation and Release

- [x] 3.1 Run focused tests, Ruff, import/compile checks, and strict OpenSpec validation.
- [x] 3.2 Restart the live launchd service and exercise one real WebSearch call through `http://127.0.0.1:2455`.
- [x] 3.3 Archive the verified OpenSpec change, commit the complete change to `main`, and push `origin/main`.
