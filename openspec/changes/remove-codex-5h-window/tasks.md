# Tasks

- [x] Server: make `UsageSummaryPayload.primary_window` /
      `UsageSummaryResponse.primary_window` optional; return null for
      codex-only scopes in `build_usage_summary_response`; drop the 100%
      primary default for openai accounts in the account mapper.
- [x] Regression tests: `/api/usage/summary?provider=openai` returns null
      primary + weekly intact; mixed pool keeps primary; openai account with
      no usage has no primary gauge (`tests/integration/test_usage_api.py`,
      `tests/unit/test_account_mappers.py`).
- [x] Dashboard: `UsageDonuts` gains `showPrimary`; dashboard page hides the
      5-Hour donut when the provider filter is `openai`; component test.
- [x] Menubar: Codex scope renders weekly-only pool card; codex account rows
      drop the 5H cell and ring off the weekly window; Swift tests updated;
      `swift build` + `swift test` green.
- [x] Validate + deploy: ruff, pytest, frontend typecheck/lint/vitest;
      restart `com.aneyman.agent-lb`; verify the live endpoint returns null
      primary for `provider=openai`; push to main and fast-forward other
      instances.
