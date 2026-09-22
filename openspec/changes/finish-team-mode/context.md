# Team mode closeout, September 19, 2026

## Result

Team mode is enabled on `https://studio.tailf266ac.ts.net:2455/team`. Jacob has one active key and aggregate caps of $5/day, $20/week, and $50/month. Windows and zsh onboarding snippets use that HTTPS origin. The key is not present in this repository or the evidence files.

The raw Tailscale TCP route on 2456 was removed because it discards client identity. The HTTPS route on 2455 remains. Existing trusted-client CIDRs and unrelated Tailscale routes were not changed.

Live OpenAI `gpt-5.6-luna` and Anthropic `claude-haiku-4-5` requests both returned `TEAM_MODE_OK` using Jacob's real member key. The ledger attributed 18 OpenAI tokens and 24 Anthropic tokens to Jacob. Sonnet returned an upstream account-pool quota error, not a team gate denial.

## Changes

- Restore the contracted five-second aggregate cache and reject cached totals from a previous UTC day, week, or month.
- Quote onboarding URLs as literal values in zsh and PowerShell.
- Reject zero/negative caps and fractional token caps rather than silently saving unlimited access.
- Keep table columns readable on phones with horizontal scrolling.
- Stop standalone OpenRouter test helpers from changing the process database during pytest collection.
- Add proxy-lifecycle, shell-literal, calendar-rollover, and migration compatibility tests.

No migration was added or altered. Existing single head: `20260918_000000_add_team_members`.

## Verification

Worktree `/Volumes/StudioExt/repos/agent-lb-worktrees/team-mode`, branch `feat/team-mode`, starting head `b74f333d`. All changes remain uncommitted. Reviewed files were also copied into the main checkout only after their previous content matched the worktree baseline. Main was concurrently advanced by another task; unrelated changes were preserved.

| Command | Result |
| --- | --- |
| `uv run pytest -q tests/unit/test_team_gate.py tests/unit/test_team_onboarding.py tests/unit/test_team_mode_proxy_auth.py tests/unit/test_team_mode_dashboard_gate.py tests/unit/test_team_mode_drain_gate.py tests/integration/test_team_api.py tests/integration/test_team_proxy_lifecycle.py tests/integration/test_team_migration.py` | Exit 0, 44 passed |
| `uv run pytest -q` | Attempted; stopped after database pollution and later an unrelated hanging test were diagnosed |
| `uv run pytest -q --timeout=30` | Exit 1, 4,297 passed, 32 failed, 43 skipped; no Team test failed |
| `uv run ruff check app clients` | Exit 0 |
| `uv run ruff format --check app` | Exit 1, 11 pre-existing files need formatting; Team files pass |
| `uv run ruff format --check app/modules/team` | Exit 0 |
| `npm run typecheck` | Exit 0 |
| `npm run lint` | Exit 0 |
| `npm test -- --run` | Exit 0, 95 files and 634 tests passed |
| `npm test -- --run src/features/team/components/team-page.test.tsx` | Exit 0, 9 passed after final table change |
| `npm run build` | Exit 0; existing large-chunk warning |
| `uv run python -c 'from alembic import command; from app.db.migrate import _build_alembic_config; command.heads(_build_alembic_config("sqlite:///:memory:"))'` | Exit 0, one head |
| `uv run agent-lb-db --db-url <isolated SQLite URL> check` | Exit 1, existing request-log index drift, unrelated to team migration |
| `npx --yes @fission-ai/openspec@latest validate finish-team-mode --strict` | Exit 0 |
| `npx --yes @fission-ai/openspec@latest validate --specs` | Exit 0, 37 passed |
| `git diff --check` | Exit 0 |

Full-suite failures concern legacy reset-credit migrations, existing index drift, stale load-balancer mocks, account mapper fixtures, model/provider/quota catalog expectations, cache invalidation, bridge prune timing, installer fixtures, and OpenSpec hygiene. The current change's tasks were unchecked during the full run and are now checked with this evidence; other OpenSpec hygiene failures remain. See evidence/backend-full-summary.txt.

The main checkout's separate focused attempt exited 4 during import because unrelated account-schema work lacks `AccountCreditsWindow`. Those files were not copied to runtime or repaired here. The feature worktree tests and live service are distinct evidence.

## Browser and runtime proof

Aside exercised the isolated backend through the real dashboard: create member, invalid cap validation, saved caps, key issuance, key count, and onboarding dialog. Aside also opened the deployed HTTPS Team page and confirmed Jacob's row. Screenshots are in evidence/.

Aside did not support viewport resizing. A local Playwright fallback used the installed Chrome binary to capture a 390px phone viewport and verify horizontal table scrolling reaches the member actions. No new browser download was installed.

Runtime checks after deployment:

- Trusted local models and dashboard: HTTP 200.
- Untrusted forwarded Jacob IP without a key: HTTP 401.
- Untrusted forwarded Jacob IP accessing dashboard routes: HTTP 403.
- Same IP with Jacob's key: models and self-service usage HTTP 200.
- Suspended Jacob: HTTP 403 `team_member_suspended`.
- Temporary token cap below recorded usage: HTTP 429 `team_member_over_cap`, `X-Team-Window: day`, and UTC reset header.
- Restored active state and normal caps: HTTP 200.
- Both provider inference checks returned the expected text and were attributed to Jacob.

These member-IP checks use the real local ingress with an explicit `X-Forwarded-For` header, not a request originating on Jacob's machine. Trusted HTTPS dashboard access was exercised over the actual Tailscale URL.

## Deployment and recovery

Runtime backup and reviewed hash manifest: `/Users/aneyman/.agent-lb/runtime/backups/team-mode-20260919T121049/`.

Only `app/modules/team/api.py`, `app/modules/team/service.py`, and built dashboard assets were deployed. Their original runtime bytes matched the feature baseline. Existing assets were retained. No broad sync ran. The startup script was verified to start the internal runtime without syncing source.

The service was drained, verified at zero in-flight requests, and restarted with `launchctl kickstart -k gui/501/com.aneyman.agent-lb`. Readiness returned 200 and draining reset to false. Final deployed hashes were verified and recorded in the backup manifest and runtime sync log. No credentials, launchd configuration, or unrelated runtime patches were replaced.

For rollback, disable team mode through the trusted settings API, restore the two Python files and static index/assets from the backup, and use the same drain/restart procedure. Do not restore the raw 2456 route while team mode is enabled.

## Remaining client-side work

Jacob is online at `jacob.tailf266ac.ts.net`, but SSH port 22 times out. The username and an enabled SSH server are pending user input. There is no claim that Jacob's actual PC has run either CLI.

Prepared outside the repository:

- `/Users/aneyman/.agent-lb/team/jacob.key`, permissions 0600.
- `/Users/aneyman/.agent-lb/team/jacob-setup.ps1`, prompts for the key and sets user-scoped Claude Code/Codex environment variables.
- `/Users/aneyman/.agent-lb/team/jacob-onboarding.txt`, key-free instructions.

The server feature is operational. Jacob-machine acceptance and the unrelated full-repository failures are not resolved by this closeout.
