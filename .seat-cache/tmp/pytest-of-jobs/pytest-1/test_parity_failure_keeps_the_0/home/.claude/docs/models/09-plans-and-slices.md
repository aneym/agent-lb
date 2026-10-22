## Plans and slices (2026-09-26; updated 2026-10-08)

Opus plans and asks `astra-consult` before splitting complex or risky work. Every slice names its files, fixed interfaces and one check, which is one user scenario on one vertical path; the seat runs it while writing and the harness reruns it on main after merge (2026-10-07). A decision the check cannot back stays on Opus. Start independent slices together; an open product call gets a seam with a reasonable default (2026-09-28). An incident fix ships with the check that catches it next time (2026-09-26).
Lint, format and typecheck go only through `factory-lint` (2026-10-17): the implementer contract runs `factory-lint fix --changed`, then `factory-lint check --changed --format json`, and the HANDOFF `check` field records that command and its exit code; nothing calls ruff, pyrefly, tsc, oxlint or shellcheck directly.
Tests: load the test-audit skill first, prefer e2e, then integration without mocks of our own code, then golden; never thin a test that guards a kept floor (2026-10-07).

