## 1. Compatibility Bridge

- [x] 1.1 Add a typed Claude Messages to OpenAI Responses request translator with the locked Sol, high-reasoning, priority profile.
- [x] 1.2 Add a stateful OpenAI Responses SSE to Claude Messages SSE translator covering text, reasoning metadata, tool calls, usage, completion, and errors.
- [x] 1.3 Add the dedicated ccdex Messages and token-count compatibility routes using OpenAI account routing and existing request accounting.

## 2. Launcher and Catalog

- [x] 2.1 Add a fail-closed ccdex launcher that runs the installed Claude Code harness and selects only the dedicated compatibility route.
- [x] 2.2 Register gpt-5.6-sol in the static model catalog and preserve requested-versus-actual service-tier reporting.
- [x] 2.3 Add installation/onboarding wiring so ccdex is an executable command on a configured machine.

## 3. Verification and Release

- [x] 3.1 Add focused translator, route, launcher, model-catalog, and regression tests.
- [x] 3.2 Run Ruff, targeted tests, import checks, launcher byte-compilation/dry-run, and strict OpenSpec validation.
- [x] 3.3 Restart the live service and exercise a real ccdex Claude Code prompt including a tool call against http://127.0.0.1:2455.
- [x] 3.4 Commit and push the validated OpenSpec, implementation, and tests to origin/main.
