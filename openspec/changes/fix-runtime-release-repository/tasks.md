# Tasks

- [x] Add an OpenSpec delta for the runtime release repository and release URL.
- [x] Point the runtime version service and response schema at `aneym/agent-lb`.
- [x] Update runtime API tests and client fixtures for the public release URL.
- [x] Validate the OpenSpec change and run focused runtime tests.
  - `npx --yes @fission-ai/openspec@latest validate fix-runtime-release-repository --strict`
    passed.
  - `uv run pytest -q tests/unit/test_runtime_version.py tests/integration/test_runtime_api.py`
    passed.
  - Read-only live daemon check: `http://127.0.0.1:2455/health` returned
    `{"status":"ok"}`, but `http://127.0.0.1:2455/api/runtime/version` still
    returned stale `releaseUrl`
    `https://github.com/Soju06/agent-lb/releases/latest` at
    `2026-06-14T03:49:10.398208Z`. No restart was performed because the daemon
    is healthy and service restarts are approval-gated; live runtime proof
    should wait for an approved restart/reinstall from the candidate.
