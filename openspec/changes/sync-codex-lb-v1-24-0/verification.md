# v1.24.0 integration evidence

## Pinned scope

- Stable upstream: `Soju06/codex-lb` v1.24.0,
  `84fde5a1ed5e0d5a58ccb3ec4b82938b059bf8c5` (latest-stable API rechecked).
- Original fork: `215823089aca206b5b8536d35415f9cc4a20e943`.
- Refreshed PR base: `cac869035fd36c31247086ce7fac890afb3586d2`.
- Original preview: 521 conflicted paths. The integration preserves both
  histories instead of replacing the fork with the upstream tree.

## Deliberate fork alternatives

The fork retains its split provider/credential-custody modules, Claude billing
and system-block guards, member and machine attribution, cockpit, launchers,
deployment workflows, and `tests/floor`/`tests/e2e` layout. Upstream unit and
integration directories remain absent; ported tests use the fork's floor layout.
The upstream scheduled-task lock is excluded as generated machine state.

OpenAI reset-credit routes use upstream cross-replica claims and idempotency;
Claude routes retain the durable provider journal and explicit manual override.
The usage scheduler combines upstream per-account refresh/anchored reset
recovery with the fork's Claude reset-window priming and uncertain-token recovery.
Account deletion remains asynchronous, with tests driving explicit drain passes.
API-key totals/trends read folded history plus the raw tail; hourly folds mask
forwarded-edge receipts from the API-key dimension to avoid duplicate billing.

Both migration branches converge at a merge revision followed by additive OAuth
provider identity, a compatible PostgreSQL usage index, and provider cache-token
rollup counters. The PostgreSQL index quotes `window`, retries partial column
application, and repairs an invalid interrupted concurrent index.

## Candidate checks

These are local candidate-tree results, not a green exact-SHA coordinator receipt.
The final PR comment identifies the published commit and final run counts.

| Check | Result |
|---|---|
| Frontend Vitest | 1,152 passed in 147 files |
| Frontend frozen dependency install, typecheck and production build | Passed |
| Built dashboard + real isolated FastAPI browser smoke | Passed, no API/page/console errors |
| Consolidated floor candidate | 1,817 passed; 19 skipped; 3 expected failures; eight hook-dispatcher files and two original-fork policy tests excluded |
| E2E | 27 passed; installed-Codex profile skipped by its explicit opt-in |
| Proxy bridge suite extracted from the pinned release | All 640 cases passed across suite and harness retry |
| Capability route suite | 152 passed |
| Forwarded request identity regressions | 4 passed |
| Source routing, settlement, cost caps and lifecycle batch | 130 passed; 12 added source-price admission regressions passed |
| Provider usage + atomic snapshot/rollup batch | 68 passed; 4 PostgreSQL-only skips in SQLite run |
| API-key retention, rollups and cost/settlement batch | 79 passed |
| Final targeted deletion/reset/scheduler regressions | 59 passed; 3 PostgreSQL-only skips in SQLite run |
| Migration floor on SQLite | 14 passed; 2 PostgreSQL-only skips |
| Empty and existing-fork SQLite upgrades | Single head; migration policy passes; no schema drift |
| Seeded SQLite fork upgrade | Account/provider/custody bytes, key/member, request attribution/cache counters, caps and settings preserved |
| Empty and existing-fork PostgreSQL upgrades | Single head; migration policy passes; no schema drift; synthetic Anthropic account preserved |
| Ruff application checks, compile and proxy architecture ratchets | Passed without relaxing ratchets |
| Wheel and sdist | Built as 1.24.0; packaged static assets match the frontend build; isolated wheel installation and CLI/migration help smoke passed |
| Strict OpenSpec change validation | Passed |

The full floor run and PostgreSQL concurrency run are recorded separately in the
final PR verification comment, including their exclusions and any failures.
The extracted upstream bridge harness used disposable fixtures and minor import,
identity-field, startup-env and heartbeat-patch adaptations for the fork layout.
It was not a run of the upstream suite unmodified.

## Outstanding readiness gates

- `factory-lint` and `trivy` are absent. `local_ci.py doctor` fails, so there is
  no complete coordinator exact-SHA CI receipt. Frontend ESLint configuration
  is absent in this inherited tree; a passing full lint gate is not claimed.
- Two policy tests failed unchanged at original fork `21582308`: the installer
  revision test cannot import `policy_files`, and the core obligation test
  expects the missing `rails merge path` text. Both failures were reproduced
  from an isolated archive of the original commit. After merging current `main`,
  the installer test passes; the remaining policy obligation failure persists
  (post-merge focused policy/route suite: 35 passed, 1 failed).
- Eight hook-dispatcher test files were omitted from the consolidated local
  floor run. An earlier installer/parity run spawned excessive child processes;
  these need an isolated runner with its policy infrastructure.
- Cross-vendor custody/spend-cap review is unavailable: this host has no
  `seat-run`, installed `verifier-local` role, or `forward-contract.py` launcher.
  Publication is a draft with an explicit verification override, not a review PASS.
- Source files exceeding the authored 20,000-byte cap are inventoried in
  `size-overages.tsv` with both parent sizes. There are 61 application and 22
  frontend source overages, plus imported tests/support files. The draft does
  not assert size-cap compliance or silently waive that policy.
- Real disposable-provider acceptance journeys remain unavailable because the
  identity backend is absent. Synthetic provider HTTP/SQL fixtures and browser
  smoke do not establish real subscription compatibility.

No operator subscription credentials, serving database, or service restart was
used for these checks. PostgreSQL used only the task-owned disposable container;
browser smoke used a fresh temporary SQLite database and disabled external loops.
