## 1. Bridge and Route

- [x] 1.1 Honor supported per-request `output_config.effort` values in the Claude-to-Responses translator, defaulting to `high`.
- [x] 1.2 Make the ccdex route's locked reasoning-effort accounting follow the translated payload.

## 2. Launcher

- [x] 2.1 Scope the `/v1/messages` rewrite to bodies naming `gpt-5.6-sol`; keep token counting on the local compatibility counter.
- [x] 2.2 Move the ccdex-mode guard inside the rewrite helper so regular `cc` behavior is unit-testable.

## 3. Verification and Release

- [x] 3.1 Rebase onto latest `origin/main`; focused unit + integration tests and full suite pass.
- [x] 3.2 Run Ruff, launcher byte-compilation, and OpenSpec validation.
- [x] 3.3 Commit and push the validated OpenSpec, implementation, and tests to `origin/main`.
- [x] 3.4 Restart the live service and prove per-task effort and Claude passthrough against http://127.0.0.1:2455.
