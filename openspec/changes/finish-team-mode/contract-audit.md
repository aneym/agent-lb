# Contract completion audit

Audited September 19, 2026 against the current worktree and the contract at `/Volumes/StudioExt/repos/homebase/docs/specs/agent-lb-team-mode/artifacts/contract-backend.md`.

| Requirement | Evidence | State |
| --- | --- | --- |
| Trusted clients remain keyless; untrusted clients need a valid prefixed key only when team mode is on | `test_team_mode_proxy_auth.py`, `test_team_proxy_lifecycle.py`; deployed local/forwarded-IP checks | Verified |
| Trusted proxy client-IP resolution preserved | Existing `request_locality.py`, proxy and dashboard gate tests, forwarded-IP route test | Verified |
| Team settings default off and are exposed in settings UI/API | Existing migration, settings schemas/repository/API, `TeamSettings`; create/upgrade tests | Verified |
| Team members, caps, model list, notes, timestamps, status, unique names | ORM, migration, team schemas and CRUD integration test | Verified |
| Indexed nullable key attachment with delete-to-null behavior | Migration and ORM FK, migration upgrade/downgrade test, issue/delete lifecycle tests | Verified |
| Single migration head and existing-row compatibility | `20260918_000000_add_team_members`; migration suite and fresh SQLite upgrade/check | Verified on SQLite |
| Suspension and model allowlist enforcement before reservation | Gate unit tests, both call-site tests, actual Responses/Messages route-denial test | Verified |
| Aggregate caps across member keys | Seeded two-key HTTP lifecycle test; live token cap denial after two successful provider calls | Verified |
| UTC calendar windows for day/week/month | Exact-boundary seeded API test; window and cache rollover tests | Verified |
| Five-second aggregate cache and expiration | Unit test reuses at 4.9 seconds, refreshes after 5.1; day/week/month rollover tests | Verified |
| 429 error type and UTC reset headers | Gate tests, lifecycle route test, deployed cap probe | Verified |
| Member identity in cached API-key data and invalidation on reassignment | `test_reassigning_key_invalidates_cached_member_identity`; existing service invalidation code | Verified |
| Member list with computed gates, usage and keys | CRUD/usage/gate-chip API and frontend tests | Verified |
| Create/update/delete members | API tests, live isolated browser create/edit flow, lifecycle deletion | Verified |
| One-time key issuance through existing service with name and expiry | API tests, expiry validation test, browser key reveal | Verified |
| Usage totals, model breakdown and daily series | Seeded API window tests including exact UTC boundaries | Verified |
| Configured public URL or request-origin fallback, key-free onboarding snippets | Onboarding API and shell-literal tests; deployed HTTPS onboarding | Verified |
| Team route/navigation, table, drawer, status/caps/models fields | Existing components, nine Team frontend tests, Aside desktop flow and phone scroll check | Verified |
| Reuse existing primitives, no new dependencies | Source diff and unchanged package manifests | Verified |
| Dashboard rejects untrusted clients before disabled/session shortcuts | Dashboard gate unit suite and real HTTP route-denial test | Verified |
| Self-service usage remains accessible with member key | Lifecycle test and deployed `/v1/usage` check | Verified |
| Whole-suite and static command list | Final results tracked in `../repair-team-mode-verification/context.md`: exact pytest command 4,344 passed, 43 skipped; all other required commands exit 0 | Verified for available local checks |
| Changed files, migration ID, commands/counts, omissions reported; uncommitted tree | `context.md`, evidence files, current git status | Recorded |

## End-to-end deployment additions

The operator's later instruction authorized self-healing and work needed to finish the goal. Team mode is deployed selectively, the unsafe raw TCP route is removed, the member is provisioned, and both OpenAI and Claude Haiku inference succeeded with the member's key. The deployed files were hash-verified and the service is healthy.

The member's own Windows machine has not run the setup script or either CLI. SSH to its Tailscale hostname still times out on port 22, and its Windows username is unknown. This is an outstanding client-side acceptance step, not evidence that the server failed. The requested username/OpenSSH clarification remains pending.

The follow-up repository verification repairs stay isolated and are not deployed to the live service or copied over unrelated main-checkout edits. PostgreSQL-only migration tests and Helm checks remain skipped in this local environment and are not claimed as verified.
