# Goal Brief - agent-lb public release readiness

- **Date:** 2026-06-13
- **Owner / requester:** Alex Neyman
- **Repo:** `/Users/aneyman/repos/agent-lb`
- **Branch:** `main` on fork `aneym/agent-lb`
- **Status:** In progress - local gates, OpenSpec validation, local package
  artifacts, and focused release-risk audits are green; account-visible GitHub
  metadata/release actions remain approval-gated
- **This brief:** `/Users/aneyman/repos/agent-lb/GOAL.md`

## Objective

Get `agent-lb` ready for public release end to end: public README and package
metadata, agent onboarding rules/skills, OpenSpec-backed behavior changes,
release screenshots, PR/release readiness evidence, and a clear handoff for any
remaining blockers.

The release is not complete until public-facing docs match the product,
behavior changes are covered by OpenSpec, tests and lint gates have current
evidence, screenshots are regenerated or explicitly accepted as current, and
GitHub PR/release state has been checked from the live service.

## Ground Rules

- Work directly on `main`; this fork intentionally does not use feature
  branches unless the user explicitly asks.
- Preserve user and other-agent work already in the dirty tree. Do not revert
  unrelated edits.
- Use OpenSpec for behavior, API, schema, CLI, dashboard-visible,
  proxy-routing, operator-contract, and compatibility changes.
- Do not edit `CHANGELOG.md` directly.
- Do not add feature or behavior docs under `docs/`; keep those notes in
  `openspec/`.
- Do not publish, push, create releases, update GitHub metadata, or open PRs
  without a fresh explicit user go-ahead.

## Current Scope

Release-readiness work in this pass covers:

- Public metadata cleanup in `README.md` and `pyproject.toml` so the package is
  described as a ChatGPT and Claude load balancer, not an OpenAI-only fork.
  The package maintainer metadata now names Alex Neyman for the public fork
  while preserving upstream authorship, package project URLs point at
  `https://github.com/aneym/agent-lb`, and `.all-contributorsrc` targets
  `aneym/agent-lb`.
- Durable artifact refresh in `GOAL.md` and `HANDOFF.md`, replacing stale
  `swap-lb` / `feat/anthropic-provider` notes.
- OpenSpec coverage for trusted-proxy API-key bypass behavior:
  `openspec/changes/harden-trusted-proxy-api-key-auth/`.
- Migration compatibility for metadata-created databases now lives in
  `app/db/migrate.py`; already-committed Alembic revision files were left as
  forward-only historical artifacts.
- Public onboarding/service-control drift was tightened: the macOS installer,
  `GETTING-STARTED.md`, the `get-started` skill, and the menubar client now
  agree on launchd label `com.aneyman.agent-lb`; README JSONC config examples
  are covered by a parser regression test. Public release docs tests now also
  pin the agent onboarding runbook/skill contract for the service label, local
  port, canonical runbook delegation, and Claude subscription-billing guardrail.
  The `get-started` skill activation rules now match public Anthropic/Claude,
  OpenAI/ChatGPT, Claude Code, Codex, OpenCode, and OpenClaw setup prompts,
  with a subprocess regression proving those prompts suggest the onboarding
  skill. The skill closeout now points `/v1` clients at the same discovered-model
  verification path as the canonical runbook.
- The `agent-lb-account-operator` skill is now wired into public skill
  activation for account, billing, subscription-ledger, browser-profile,
  pause/reactivate, remove, and verification prompts. The skill now requires
  agents to clarify OpenAI/ChatGPT vs Anthropic/Claude before touching browser
  state or API rows when the provider is not named, and its example registry
  includes both OpenAI and Anthropic dedicated Chrome profile entries with null
  account identifiers and no-secrets notes.
  `GETTING-STARTED.md` now hands ongoing quota reset, stuck/rate-limited
  account, pause/reactivate routing, billing/subscription, and browser-profile
  work to that skill plus the local `.agent-lb/account-profiles.json` registry.
  `AGENTS.md` now exposes the same account-operations handoff for agents
  entering through the repo instructions instead of the onboarding runbook.
  `README.md` now gives public readers the same post-setup "account operator"
  cue for quota, stuck/rate-limited account, billing/subscription,
  pause/reactivate routing, verification, and browser-profile work.
  Public skill activation rules now cover quota reset checks, stuck/disabled or
  rate-limited accounts, subscription/account status, routing imbalance, and
  pause/reactivate routing support prompts. The account-operator skill body now
  explicitly covers those account-specific support paths, not only add/cancel
  browser-profile work.
- The README's paste-ready "For AI Agents" prompt now carries the same
  high-risk onboarding guardrails as the canonical runbook: connect
  Claude/ChatGPT accounts one at a time, never set Claude API/auth token env
  vars when routing through the load balancer, and show dotfile edits before
  applying them.
- Public beta install examples now match the current prerelease artifact model:
  source checkout is the always-available service/API path, fresh clones point
  to CLI account auth or a frontend build before dashboard use, Docker examples
  pin `ghcr.io/aneym/agent-lb:1.20.0-beta.3`, uvx examples pin
  `agent-lb==1.20.0b3`, and Helm OCI examples include
  `--version 1.20.0-beta.3 --devel` for both install and upgrade commands.
  Helm chart metadata now describes the ChatGPT and Claude public fork. Public
  release docs tests also pin generated release-workflow and beta-publish
  prerelease install notes so prereleases keep pinned uvx/Docker/Helm `--devel`
  commands while stable releases keep the latest-safe install commands. The
  runtime-portability context for Codex session retagging also pins its Docker
  examples to the current prerelease image instead of the unpublished `latest`
  tag. The `Publish Beta Release` workflow now dispatches `release.yml` when a
  matching GitHub prerelease already exists, so editing an existing beta
  prerelease no longer skips PyPI, Docker, Helm, and release-asset publication.
  The `Release` workflow now scopes concurrency by the selected release tag for
  both GitHub release events and manual dispatches.
- Release-managed CODEOWNERS now include `@aneym` alongside upstream ownership
  for release-please files, `CHANGELOG.md`, `app/__init__.py`, and `uv.lock`,
  so the public fork owner is named on release-critical paths.
- GitHub automation defaults are now covered by public-release regression tests:
  the all-contributors checker defaults to `aneym/agent-lb`, the Codex review
  label sync required-check map is keyed by `aneym/agent-lb`, and neither script
  may drift back to `Soju06/agent-lb`.
- `.github/CONTRIBUTING.md` now documents the automated beta release-candidate
  path: the synced `release/beta-*` PR, candidate-SHA validation checklist,
  publish guard, and dirty-tree publishing warning.
- The beta release guard now requires exactly one live upstream/account smoke
  checklist choice. Release-candidate PRs that check both the live-smoke and
  not-required items fail before beta tag or prerelease publication, and the
  generated checklist plus release-management OpenSpec now document that rule.
- Release-managed version checks now account for `uv.lock` using PEP
  440-normalized prerelease spellings (`1.20.0b3`) while `pyproject.toml`,
  app, frontend, and Helm metadata keep the tag spelling
  (`1.20.0-beta.3`). The release-management OpenSpec now records that logical
  version agreement rule.
- GitHub issue/discussion intake forms and `SECURITY.md` now track the current
  `1.20.0-beta.3` release train instead of stale `1.17.0`/`1.16.0` examples.
The bug/account/feature templates now use provider-neutral wording for
ChatGPT and Claude pools, and the bug form spells `OpenCode` with ASCII
characters. The bug form now names the advertised public clients directly:
Codex, Claude Code, OpenCode, OpenClaw, OpenAI-compatible SDKs, and
Anthropic-compatible SDKs; the feature request form includes the
Anthropic-compatible API surface and client launchers/integrations as scoped
areas. Public bug, account/quota, feature-request, and Q&A templates now route
security vulnerability reports to GitHub private advisories instead of public
intake threads. The PR template now tells contributors to preserve provider-faithful
OpenAI/Codex and Anthropic/Claude wire formats for protocol-sensitive paths
and requires screenshots or a clear not-applicable reason for
dashboard/UI-visible changes. It also requires public client/onboarding
  changes to keep `AGENTS.md`, `README.md`, `GETTING-STARTED.md`, the
  `get-started` skill, the public skill activation rules, and the
  public-release docs regression test in sync, or explain unaffected surfaces.
  It also requires public client, release-version, account-plan, and
  support-intake changes to keep the bug report, account quota, feature-request,
  and Q&A intake forms plus public-release regression coverage in sync.
  Security/support-window changes now also have to keep `SECURITY.md`, README,
  Helm README, and public-release regression coverage in sync when supported
  versions, release train, artifact names, vulnerability reporting, or
  published-artifact security wording changes.
  Contributor gates now also require account admin, dedicated browser-profile,
  billing, subscription-ledger, pause/reactivate, removal, and verification
  guidance to keep `AGENTS.md`, README, `GETTING-STARTED.md`, the
  `agent-lb-account-operator` skill, its example account profile registry,
  public skill activation rules, and public-release regression coverage in sync.
- Runtime update-check drift was tightened:
  `openspec/changes/fix-runtime-release-repository/` makes `/api/runtime/version`
  check and link to the public `aneym/agent-lb` release repository instead of
  the old upstream repo.
  Read-only live daemon check: the launchd service on
  `http://127.0.0.1:2455` is healthy and reports
  `currentVersion` `1.20.0-beta.3`, but
  `http://127.0.0.1:2455/api/runtime/version` still returned stale
  `releaseUrl` `https://github.com/Soju06/agent-lb/releases/latest` with
  response `checkedAt` `2026-06-14T04:35:55.088884Z` during a read-only check
  at `2026-06-14T04:36:45Z`; source/tests still assert the candidate
  `https://github.com/aneym/agent-lb/releases/latest` URL. No restart was
  performed because the service is healthy and service restarts are
  approval-gated. The read-only runtime proof
  `./scripts/public-release-runtime-proof.sh v1.20.0-beta.3` now fails closed
  with expected non-zero `rc=1` before an approved restart/reinstall: health
  passed, version metadata parsed, and the runtime assertion returned `false`
  at `2026-06-14T07:19:03Z` after printing
  `runtimeProofAt=2026-06-14T07:19:03Z`; the healthy daemon still serves the
  stale upstream release URL.
- Main OpenSpec `Purpose` placeholders from archived changes were replaced
  with concise capability purposes so the spec tree no longer exposes
  archive-generated placeholder text. The opsx sync command and matching skill
  now instruct agents to write concise capability purposes instead of leaving
  placeholder text in new main specs.
- A targeted release audit found that historical DB rows with mixed-case or
  padded subscription statuses could leak raw values through account summaries.
  `app/modules/accounts/mappers.py` now normalizes ledger status values before
  API serialization, with an integration regression for existing rows.
- `.github/SECURITY.md` now describes Docker, Helm, and PyPI as published
  artifacts once available, matching the approval-gated public artifact state
  instead of implying the current beta image/chart/package is already visible.
- A final local dirty-diff release audit covered the remaining high-risk slices:
  subscription visibility, trusted-proxy auth, runtime release links, Anthropic
  quota diagnostics, HTTP bridge compatibility, migration/provider defaults,
  and macOS/frontend status sync. No additional local code blockers were found.
- `scripts/public-release-drift-scan.sh` now turns the final public-surface drift
  audit into a reusable preflight gate for stale project names, old release
  examples, deleted screenshot artifacts, unpublished Docker `latest` install
  shortcuts, and stale hosted-release descriptions across README, onboarding,
  Helm docs, GitHub templates/workflows, skills, main OpenSpec specs, and
  package metadata.
- `HANDOFF.md` now includes an approval packet with the exact live repo
  metadata target, homepage, topic set, resource links, replacement prerelease
  notes, release-tag guardrails, and a post-publish proof script plus expanded
  command contract for GitHub/PyPI/GHCR artifacts, public repository metadata,
  and release-body freshness. Public release docs tests
  now pin the README
  GitHub metadata header against that approval packet so the
  description/homepage/topics/resources cannot drift silently. The staged topic
  update uses GitHub's replace-all topics API rather than add-only topic flags,
  so stale public topics are removed when the approved command runs. The same
  regression suite now parses the staged replacement prerelease notes and
  rejects the obsolete SQLite migration caveat from the old live prerelease
  body, pins the exact-one live upstream/account smoke guard in the staged
  notes, and pins the fail-closed post-publish proof script for PyPI version
  plus exact wheel/sdist filenames, pip-index, GHCR image tags, the Helm chart
  package, GitHub repository metadata, GitHub release title, public release URL,
  non-empty published timestamp, exact wheel/sdist assets, and the replacement
  release body. The
  approval packet now also includes a read-only/local commit and PR readiness
  preflight for the final status, diff, live PR/run, dependency lock,
  release-version regression, public-doc, beta-guard, OpenSpec, Ruff, and
  whitespace checks before any approved public mutation. It now also includes a
  paste-ready PR draft with summary, test plan, screenshot paths,
  approval-gated publication status, and OpenSpec coverage; public-release docs
  tests pin that draft so it cannot silently drop release proof or mutate-action
  boundaries. The contributor and PR-template release proof gates now name the
  local artifact proof, live blocker snapshot, PR-head proof, pre-publish
  readiness guard, and post-approval artifact proof scripts
  `./scripts/public-release-local-artifact-proof.sh <approved-release-tag>`,
  `./scripts/public-release-live-snapshot.sh <approved-release-tag>`,
  `./scripts/public-release-pr-head-proof.sh <pr-number>`,
  `./scripts/public-release-runtime-proof.sh <approved-release-tag>`,
  `./scripts/public-release-publish-readiness.sh <approved-release-tag>`, and
  `./scripts/public-release-postpublish-proof.sh <approved-release-tag>`. The
  preflight is also available as the read-only
  `./scripts/public-release-preflight.sh <approved-release-tag>` command.
  The publish-readiness guard is also staged as
  `./scripts/public-release-publish-readiness.sh <approved-release-tag>` and
  fails closed when the selected tag does not point at `HEAD`, the working
  tree is dirty, or the returned current-head `main` workflow evidence is
  missing/non-green.
- Agent git workflow policy now matches the public fork split:
  `aneym/agent-lb` work stays on `main`, while branch/PR conventions apply to
  upstream `Soju06/codex-lb` contribution work. Beta guard, cleanup, and Codex
  label automation tests now model `aneym/agent-lb` as the active release repo.
- Active OpenSpec-backed work already present in the tree:
  `hide-canceled-subscription-accounts`,
  `harden-trusted-proxy-api-key-auth`, `fix-runtime-release-repository`,
  `fix-anthropic-quota-selection-diagnostics`,
  `fix-menubar-limit-status-sync`, and
  `require-beta-candidate-validation`.
  A follow-up OpenSpec hygiene pass also cleared stale CLI-unavailable
  validation tasks for older active changes now that the
  npm-distributed `@fission-ai/openspec` CLI path is documented.
  A full active-change sweep on 2026-06-14T02:03:58Z strict-validates all
  54 active OpenSpec changes. The previously empty
  `decompose-proxy-service` change now has a normative
  `proxy-service-architecture` delta, and the release-doc regression suite now
  fails if an active change lacks spec-delta headers. The approval preflight now
  runs the reusable `./scripts/validate-active-openspec-changes.sh` sweep before
  any approved public mutation. The active OpenSpec task ledger now records
  read-only live PR/run evidence for both remaining unchecked PR-head
  CI/Codex-review gates, and public-release docs tests fail if any active
  unchecked task is not one of those gated PR-head confirmations.
- Release screenshot regeneration for dashboard, accounts, settings, login, and
  dark-mode public images under `docs/screenshots/`. Public release docs tests
  now pin the seven README screenshot references and verify each referenced
  JPEG is present at the expected 2880x1800 Playwright capture size. The
  screenshot directory is also pinned to those seven README-backed assets, so
  older tracked but unreferenced screenshot artifacts cannot creep back into
  the public-release bundle. A direct `file docs/screenshots/*.jpg` and
  README/reference audit on 2026-06-14T04:18:50Z confirmed exactly those seven
  `2880x1800` JPEGs remain and no public docs reference the deleted
  `apis-assigned-accounts` or `codex-session-retag-*` artifacts. The
  screenshot harness now uses a repo-owned `127.0.0.1:4174` Playwright preview
  URL and refuses existing-server reuse, preventing stale local apps on common
  preview ports from being captured as release screenshots.

## Verification Ledger

Current local evidence from this release-readiness pass, refreshed through the
2026-06-14T08:38:25Z full read-only public-release preflight:

- `uv run pytest -q`
  - Result: `3675 passed, 43 skipped, 4 warnings in 213.60s`
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make lint`
  - Result: proxy architecture checks passed; Ruff check passed; Ruff format
    check passed (`661 files already formatted`)
- `uvx ruff format --check . && uvx ruff check .`
  - Result: `661 files already formatted`; `All checks passed!`
- `git diff --check`
  - Result: clean
- `npx --yes @fission-ai/openspec@latest validate hide-canceled-subscription-accounts --strict`, `fix-runtime-release-repository --strict`, `harden-trusted-proxy-api-key-auth --strict`, `fix-anthropic-quota-selection-diagnostics --strict`, `fix-menubar-limit-status-sync --strict`, `require-beta-candidate-validation --strict`, and `npx --yes @fission-ai/openspec@latest validate --specs`
  - Result: all 6 release-relevant active changes valid on the 2026-06-14T01:33:06Z refresh; all specs valid (`30 passed, 0 failed`)
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make package`
  - Result: frontend production build passed, import smoke passed, Hatch built
    the wheel from the sdist, and `scripts/verify-wheel-assets.py` passed
- `ls -lh dist`
  - Result: wheel `1.3M`, sdist `1.1M` (`agent_lb-1.20.0b3.*`)
- `tar -tzf dist/agent_lb-1.20.0b3.tar.gz | rg '^agent_lb-1\.20\.0b3/(\.agents|\.github|clients|frontend|tests|docs|openspec|\.build|__pycache__|\.venv|node_modules)(/|$)' || true`
  - Result: no matches; the source distribution no longer contains top-level
    dev, client, test, screenshot, or generated build directories
- `uvx --from twine==6.2.0 twine check dist/*`
  - Result: wheel and sdist passed metadata validation
- `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3`
  - Result: tag/version mapping passed (`pypi_version=1.20.0b3`)
- `cd frontend && bun run test && bun run screenshots`
  - Result: `89 passed (89)` test files, `589 passed (589)` tests;
    screenshots `7 passed`
- `cd frontend && bun run lint`
  - Result: passed
- `cd frontend && bun run build`
  - Result: passed
- `cd clients/macos-menubar && swift test`
  - Result: `111 tests, 0 failures`
- `uv run pytest -q tests/integration/test_http_responses_bridge.py`
  - Result: `77 passed in 10.15s`
- `uv run pytest -q tests/integration/test_proxy_sticky_sessions.py tests/integration/test_repositories.py tests/integration/test_migrations.py tests/unit/test_db_migrate.py tests/unit/test_images_schemas.py tests/unit/test_model_refresh_scheduler.py tests/integration/test_settings_api.py`
  - Result: `209 passed, 3 skipped, 2 warnings in 17.51s`
- `uv run pytest -q tests/unit/test_trusted_proxy_client_ip.py tests/integration/test_trusted_proxy_auth.py`
  - Result: `15 passed in 0.66s`
- `uv run pytest -q tests/unit/test_accounts_service_transitions.py tests/unit/test_proxy_load_balancer_refresh.py tests/integration/test_anthropic_proxy.py tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py`
  - Result: `102 passed, 3 skipped in 4.97s`
- `uv run pytest -q tests/unit/test_claude_lb_launch.py`
  - Result: `10 passed in 0.02s`
- `uv run pytest -q tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py tests/integration/test_public_usage.py tests/unit/test_proxy_load_balancer_refresh.py tests/integration/test_migrations.py tests/unit/test_db_migrate.py`
  - Result: `131 passed, 6 skipped, 2 warnings in 15.85s`
- `uvx ruff format --check . && uvx ruff check . && git diff --check`
  - Result: `661 files already formatted`; `All checks passed!`;
    `git diff --check` clean
- `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `4 passed in 0.03s`
- `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `19 passed in 0.07s`; additionally covers the README's
    paste-ready AI-agent onboarding prompt for the canonical runbook pointer,
    one-account-at-a-time OAuth flow, Claude subscription-billing guardrail,
    and dotfile-approval instruction.
- `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `19 passed in 0.07s`; additionally covers public GitHub intake
    forms naming Claude Code, OpenClaw, OpenAI-compatible SDKs,
    Anthropic-compatible SDKs, the Anthropic-compatible API surface, and client
    launchers/integrations.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `10 passed in 0.06s`; covers README/Helm prerelease artifact pins,
    chart metadata, package metadata, README JSONC snippets, and Kubernetes
    version policy.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `17 passed in 0.05s`; additionally covers Helm OCI upgrade pins,
    generated release-workflow install notes, release-managed CODEOWNERS naming
    `@aneym`, and agent onboarding runbook/skill invariants.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `20 passed in 0.06s`; additionally covers GitHub issue/discussion
    version placeholders, the security-policy supported-version train,
    provider-neutral ChatGPT/Claude intake options, and ASCII `OpenCode`
    spelling.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `22 passed in 0.07s`; additionally covers public intake forms for
    Claude Code, OpenClaw, OpenAI-compatible SDKs, Anthropic-compatible SDKs,
    Anthropic-compatible API scope, and client launcher/integration scope.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `45 passed`; additionally covers the seven public README
    screenshot references, verifies each referenced JPEG exists at 2880x1800,
    and pins the README GitHub metadata header against the `HANDOFF.md`
    approval packet description/homepage/topics/resources and staged replacement
    prerelease notes without the obsolete SQLite migration caveat, plus the
    public PR
    template's dual-provider protocol-faithfulness guidance, the CONTRIBUTING
    beta release-candidate flow, and the `Publish Beta Release` prerelease
    notes/update path, including the existing-prerelease artifact-dispatch
    branch, plus `get-started` skill activation for public Anthropic/Claude,
    OpenAI/ChatGPT, Claude Code, Codex, OpenCode, and OpenClaw setup prompts,
    plus unique public-client rows, provider-specific surface guidance, and
    source-checkout API vs unbuilt-dashboard guidance, release workflow
    tag-scoped concurrency,
    canonical onboarding endpoint coverage for the README Client
    Setup matrix, OpenClaw coverage in public repo topics plus package and Helm
    chart keywords, a discovered-model `/v1` smoke path for OpenCode, OpenClaw,
    and SDK users, including the `get-started` skill's final-verification
    contract, fail-closed post-publish proof script plus expanded command
    contract for PyPI, pip-index, GHCR image tags, the Helm chart package, and
    GitHub release assets, the package `Development Status :: 4 - Beta` classifier, a
    timeless UTC quota-reset placeholder in the public account/quota intake form, the
    explicit OpenAI/Anthropic API-key-only no-account issue-template labels,
    published-artifact wording in `SECURITY.md`,
    the read-only commit/PR readiness preflight in `HANDOFF.md`, including its
    locked-dependency and release-version regression checks, and the PR
    template's screenshot-proof requirement for dashboard/UI-visible changes.
- `uv run pytest -q tests/unit/test_guard_beta_release.py`
  - Result: `10 passed`; covers beta release guard evidence parsing,
    including the mutually exclusive live upstream/account smoke checklist
    choices.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `53 passed`; covers public release docs, Kubernetes
    version policy, the CONTRIBUTING exact-one live-smoke checklist rule, and
    the staged prerelease notes' exact-one live-smoke guard, plus the
    commit/PR readiness preflight guard, PR template screenshot-proof guard,
    and server-side SDK/app onboarding guard for Anthropic-compatible SDKs,
    Vercel AI SDK, OpenAI-compatible SDKs, deployed loopback URLs, and
    browser-direct code, plus the README's Anthropic Python SDK bearer-auth
    example and Vercel AI SDK `createOpenAI` base URL override example,
    and PR/contributor release-artifact evidence gates for package, Helm,
    Docker, PyPI/GHCR, GitHub release assets, metadata, and screenshots, plus
    the PR/contributor public client/onboarding sync gate for README,
    `GETTING-STARTED.md`, the `get-started` skill, and public docs
    regression coverage, plus explicit OpenAI/Anthropic API-key-only
    no-account choices in GitHub intake forms, plus published-artifact wording
    in `SECURITY.md`, plus README/Helm guidance that OCI chart commands are
    approval-gated until the beta chart artifact is published and source-chart
    install commands remain available before publication, plus
    account-operator skill activation, dual-provider profile examples,
    provider clarification, no-secrets, billing-action confirmation guardrails,
    and PR/contributor account-operator sync gates.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `53 passed`; rerun after account-operator skill wiring,
    dual-provider profile examples, and PR/contributor account-operator sync
    gates.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `53 passed in 0.52s`; rerun after adding
    `.agents/skills/skill-rules.json` to the PR/contributor sync gates for
    public client/onboarding and account-operator changes.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `53 passed in 0.43s`; rerun after adding `AGENTS.md` to the
    PR/contributor public client/onboarding sync gate.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `54 passed in 0.43s`; rerun after adding the public
    support-intake sync gate for bug report, account quota, feature-request,
    and Q&A templates.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `55 passed in 0.39s`; rerun after adding the security/support
    policy sync gate for supported-version, release-train, package/container
    artifact, vulnerability-reporting, and published-artifact security wording
    changes.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `56 passed in 0.47s`; rerun after routing every public intake
    template's security-vulnerability reports to GitHub private advisories.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `59 passed`; rerun after routing ongoing
    account/quota/status and routing-imbalance support prompts from
    `README.md`, `AGENTS.md`, `GETTING-STARTED.md`, and
    `.agents/skills/skill-rules.json` to the account-operator skill.
    The same suite now pins PR/contributor account-operator sync gates so
    account guidance changes keep those public surfaces aligned with the skill,
    example registry, activation rules, and regression tests.
- `uv run python -m json.tool .agents/skills/skill-rules.json`
  - Result: passed; skill activation registry remains valid JSON.
- `uv run python -m json.tool .agents/skills/agent-lb-account-operator/account-profiles.example.json`
  - Result: passed; account profile registry example remains valid JSON.
- `uvx ruff format --check tests/unit/test_public_release_docs.py`
  - Result: `1 file already formatted`
- `uvx ruff check tests/unit/test_public_release_docs.py`
  - Result: `All checks passed!`
- `git diff --check`
  - Result: clean
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `60 passed`; rerun after adding the paste-ready PR draft
    guard and aligning the live blocker snapshot assertion to
    `2026-06-14T01:51:15Z`.
- `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict`
  - Result: `Change 'require-beta-candidate-validation' is valid` after
    refreshing the pending PR-head CI/Codex-review task evidence to
    `2026-06-14T01:51:15Z`.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `59 passed`; rerun after expanding the PR-template account-operator
    sync gate to pin pause/reactivate, removal, and verification guidance
    alongside browser-profile, billing, and subscription-ledger changes.
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_cleanup_superseded_beta_prs.py tests/unit/test_sync_codex_ok_labels.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `109 passed in 4.45s`; broader release-facing preflight covering release
    version normalization, beta publish guards, superseded-beta cleanup, Codex
    review label sync, public release docs, and Kubernetes version policy.
- `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3 --require-channel beta`
  - Result: tag/version mapping passed with `channel=beta` and
    `pypi_version=1.20.0b3`.
- `npx --yes @fission-ai/openspec@latest validate --specs`
  - Result: all specs valid (`30 passed, 0 failed`).
- `uv lock --locked`
  - Result: current lockfile accepted (`Resolved 120 packages in 4ms`).
- `curl -fsS http://127.0.0.1:2455/health`
  - Result: `{"status":"ok"}`; local service remained healthy without restart.
- `cd frontend && bun run test`
  - Result: `89 passed (89)` test files, `589 passed (589)` tests.
- `cd frontend && bun run screenshots`
  - Result: `7 passed` on 2026-06-14T03:17:46Z; regenerated dashboard,
    accounts, settings, login, and dark-mode public screenshots after hardening
    the harness to use the repo-owned `127.0.0.1:4174` preview URL.
- `sips -g pixelWidth -g pixelHeight docs/screenshots/{dashboard,dashboard-dark,accounts,accounts-dark,settings,settings-dark,login}.jpg`
  - Result: all seven public README screenshots are `2880x1800`; local visual
    inspection of `dashboard.jpg` and `accounts.jpg` showed nonblank,
    correctly framed UI with the `1.20.0-beta.3` footer visible.
- `file docs/screenshots/*.jpg` plus README/reference scans on
  2026-06-14T04:18:50Z
  - Result: exactly seven screenshot JPEGs remain under `docs/screenshots/`;
    each is `2880x1800`, README references only those seven public
    screenshots, and no public docs reference the deleted
    `apis-assigned-accounts` or `codex-session-retag-*` screenshot artifacts.
- `cd frontend && bun run lint`
  - Result: passed.
- `cd frontend && bun run build`
  - Result: passed.
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make package`
  - Result: frontend build, import smoke, wheel/sdist build, and
    `scripts/verify-wheel-assets.py` passed.
- `uvx --from twine==6.2.0 twine check dist/*`
  - Result: wheel and sdist passed metadata validation.
- `tar -tzf dist/agent_lb-1.20.0b3.tar.gz | rg '^agent_lb-1\.20\.0b3/(\.agents|\.github|clients|frontend|tests|docs|openspec|\.build|__pycache__|\.venv|node_modules)(/|$)' || true`
  - Result: no matches; the source distribution excludes top-level dev,
    client, test, screenshot, OpenSpec, and generated build directories.
- `uv sync --dev --frozen`
  - Result: checked `96` packages and restored the dev environment after the
    package-only sync.
- Stale blocker-snapshot marker scan across `GOAL.md`, `HANDOFF.md`, and
  `tests/unit/test_public_release_docs.py`
  - Result: no matches after the final blocker snapshot and focused-test
    alignment.
- `uvx ruff check tests/unit/test_public_release_docs.py`
  - Result: `All checks passed!`
- `uv lock --locked`
  - Result: current lockfile accepted (`Resolved 120 packages in 4ms`); keeps the
    final approval preflight from running on a stale dependency graph.
- `./scripts/public-release-preflight.sh v1.20.0-beta.3`
  - Result: passed on 2026-06-14T04:51:13Z; covers read-only status/diff,
    approved tag mapping, live PR/run checks, locked dependencies,
    release helper syntax checks including the drift-scan, PR-head proof, and
    local artifact proof helpers, public release drift scan,
    release-version/public-doc/Kubernetes tests, beta guard, release-version
    verifier, active OpenSpec sweep, main spec validation, Ruff, and whitespace.
  - Refresh: passed again on 2026-06-14T05:31:47Z after the preflight script
    began printing `preflightAt=2026-06-14T05:31:47Z` and publish-readiness
    began verifying tag/channel mapping before the dirty-tree block.
  - Current-tree refresh: passed again on 2026-06-14T05:43:20Z after printing
    `preflightAt=2026-06-14T05:43:20Z`; included read-only PR/run checks
    (`[]`/`[]`), locked dependency verification, helper syntax checks, drift
    scan, local artifact proof, release-version/public-doc/Kubernetes tests,
    beta guard, release-version verifier, `validated 54 active changes`, main
    specs `30 passed, 0 failed`, Ruff, and whitespace.
  - Current-tree refresh after PR draft cleanup: passed again on
    2026-06-14T05:47:09Z after printing
    `preflightAt=2026-06-14T05:47:09Z`; included read-only PR/run checks
    (`[]`/`[]`), locked dependency verification, helper syntax checks, drift
    scan, local artifact proof (`localArtifactProofAt=2026-06-14T05:47:10Z`),
    `106 passed`, beta guard, release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace.
  - Current-tree refresh after wiring the live blocker snapshot into the
    preflight: passed again on 2026-06-14T05:51:19Z after printing
    `preflightAt=2026-06-14T05:51:19Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T05:51:19Z`), local artifact proof
    (`localArtifactProofAt=2026-06-14T05:51:22Z`), `106 passed`, beta guard,
    release-version verifier, `validated 54 active changes`, main specs
    `30 passed, 0 failed`, Ruff, and whitespace.
  - Current-tree refresh after aligning the PR draft to the live snapshot:
    passed again on 2026-06-14T06:03:29Z after printing
    `preflightAt=2026-06-14T06:03:29Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:03:30Z`,
    `snapshotOptionalFailures=5`), local artifact proof
    (`localArtifactProofAt=2026-06-14T06:03:33Z`), `106 passed`, beta guard,
    release-version verifier, `validated 54 active changes`, main specs
    `30 passed, 0 failed`, Ruff, and whitespace.
  - Current-tree refresh after naming live-snapshot optional failures: passed
    again on 2026-06-14T06:11:15Z after printing
    `preflightAt=2026-06-14T06:11:15Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:11:16Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:11:19Z`),
    `106 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace.
  - Current-tree refresh after adding the live-snapshot blocker summary: passed
    again on 2026-06-14T06:16:55Z after printing
    `preflightAt=2026-06-14T06:16:55Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:16:56Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:16:58Z`),
    `106 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace.
  - Current-tree refresh after the publish-readiness success marker: passed
    again on 2026-06-14T06:25:32Z after printing
    `preflightAt=2026-06-14T06:25:32Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:25:33Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:25:36Z`),
    `106 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace.
  - Current-tree refresh after the PR-head evidence guard update: passed again
    on 2026-06-14T06:36:13Z after printing
    `preflightAt=2026-06-14T06:36:13Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:36:14Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:36:17Z`),
    `107 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace.
  - Current-tree refresh after adding release-state blockers to the live
    snapshot: passed again on 2026-06-14T06:44:35Z after printing
    `preflightAt=2026-06-14T06:44:35Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:44:36Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:44:39Z`),
    `107 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace.
  - Current-tree refresh after adding live-snapshot repo-state blockers:
    passed again on 2026-06-14T06:49:17Z after printing
    `preflightAt=2026-06-14T06:49:17Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:49:17Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:49:20Z`),
    `107 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. The added repo visibility, private/archive, and default-branch
    checks found no new blockers.
  - Current-tree refresh after exact release-asset proof hardening: passed
    again on 2026-06-14T06:55:29Z after printing
    `preflightAt=2026-06-14T06:55:29Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T06:55:29Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:55:32Z`),
    `107 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. The release asset blocker now requires the exact
    `agent_lb-1.20.0b3-py3-none-any.whl` and `agent_lb-1.20.0b3.tar.gz`
    GitHub release assets. The release workflow now also verifies the built
    `dist/` directory contains only the exact wheel and sdist filenames before
    the generic `dist/*` upload step can run.
  - Current-tree refresh after exact release-workflow dist hardening: passed
    again on 2026-06-14T07:03:23Z after printing
    `preflightAt=2026-06-14T07:03:23Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:03:24Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:03:27Z`),
    `108 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. Public repo metadata, release body, PyPI, GHCR, and exact
    release assets remain approval-gated/unpublished/stale.
  - Latest full preflight refresh after the live-snapshot evidence split: passed
    again on 2026-06-14T07:15:03Z after printing
    `preflightAt=2026-06-14T07:15:03Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:15:04Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:15:07Z`),
    `108 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. Public repo metadata, release body, PyPI, GHCR, and exact
    release assets remain approval-gated/unpublished/stale.
  - Latest full preflight refresh after the PR-head evidence refresh: passed
    again on 2026-06-14T07:22:54Z after printing
    `preflightAt=2026-06-14T07:22:54Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:22:55Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:22:57Z`),
    `108 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. Public repo metadata, release body, PyPI, GHCR, and exact
    release assets remain approval-gated/unpublished/stale.
  - Latest full preflight refresh after release identity blocker hardening:
    passed again on 2026-06-14T07:37:07Z after printing
    `preflightAt=2026-06-14T07:37:07Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:37:08Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:37:10Z`),
    `108 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. Public repo metadata, release body, PyPI, GHCR, and exact
    release assets remain approval-gated/unpublished/stale.
  - Latest full preflight refresh after the live evidence refresh: passed again
    on 2026-06-14T07:48:03Z after printing
    `preflightAt=2026-06-14T07:48:03Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:48:04Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:48:07Z`),
    `108 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. Public repo metadata, release body, PyPI, GHCR, and exact
    release assets remain approval-gated/unpublished/stale.
  - Latest full preflight refresh after the PyPI filename proof hardening:
    passed again on 2026-06-14T07:56:47Z after printing
    `preflightAt=2026-06-14T07:56:47Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:56:48Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:56:51Z`),
    `108 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. The PyPI JSON probe now requires the exact
    `agent_lb-1.20.0b3-py3-none-any.whl` and `agent_lb-1.20.0b3.tar.gz`
    filenames when the package becomes visible.
  - Latest full preflight refresh after the live/readiness evidence refresh:
    passed again on 2026-06-14T08:38:25Z after printing
    `preflightAt=2026-06-14T08:38:25Z`; included read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T08:38:26Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T08:38:29Z`),
    `109 passed`, beta guard (`10 passed`), release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace. Public repo metadata, release body, PyPI, GHCR, and exact
    release assets remain approval-gated/unpublished/stale.
- `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `79 passed`; covers the public-release evidence ledgers, release
    workflow artifact gates, and approval packet.
- Post-publish proof diagnostics hardening on 2026-06-14T08:05:15Z:
  `scripts/public-release-postpublish-proof.sh` now prints the expected PyPI
  version and exact wheel/sdist filenames before the PyPI JSON gate, so a
  published-but-misnamed artifact failure includes the expected public
  filenames in the operator output.
  - Read-only proof refresh: `./scripts/public-release-postpublish-proof.sh
    v1.20.0-beta.3` printed `postpublishProofAt=2026-06-14T08:06:34Z`,
    `expectedPypiVersion=1.20.0b3`,
    `expectedPypiWheelAsset=agent_lb-1.20.0b3-py3-none-any.whl`, and
    `expectedPypiSdistAsset=agent_lb-1.20.0b3.tar.gz`, then exited expected
    non-zero at the still-unpublished PyPI JSON 404 (`curl: (56)`).
  - PyPI identity refresh on 2026-06-14T08:20:38Z: the proof now also prints
    and verifies the expected PyPI summary and project URLs. A read-only
    `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3` run printed
    `expectedPypiSummary=ChatGPT and Claude account load balancer & proxy with
    usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints` and
    `expectedPypiProjectUrls={"Homepage":"https://github.com/aneym/agent-lb","Repository":"https://github.com/aneym/agent-lb","Issues":"https://github.com/aneym/agent-lb/issues","Releases":"https://github.com/aneym/agent-lb/releases"}`,
    then exited expected non-zero at the still-unpublished PyPI JSON 404
    (`curl: (56)`). The matching live snapshot at
    `snapshotAt=2026-06-14T08:20:38Z` kept
    `snapshotOptionalFailures=5` and the same blocker names while showing the
    PyPI JSON predicate now checks version, summary, project URLs, and exact
    wheel/sdist filenames.
  - Verification at 2026-06-14T08:21:36Z: shell syntax passed; public release
    docs reported `78 passed`; release-version/public-doc/K8s tests reported
    `108 passed`; `require-beta-candidate-validation` strict validation passed;
    main specs reported `30 passed, 0 failed`; Ruff and whitespace were clean.
  - OpenSpec contract alignment at 2026-06-14T08:26:07Z: the active
    release-management delta now explicitly requires the post-publish PyPI
    summary/project-URL/exact wheel-sdist identity checks, live-snapshot PyPI
    identity visibility, publish-readiness local-main evidence, and PR-head
    `headRefOid`/Codex `head=` SHA matching contract already enforced by the
    helper scripts. Public release docs reported `79 passed`;
    release-version/public-doc/K8s tests reported `109 passed`;
    `require-beta-candidate-validation` strict validation passed; main specs
    reported `30 passed, 0 failed`; Ruff and whitespace were clean.
  - Verification: `bash -n scripts/public-release-postpublish-proof.sh` passed;
    `uv run pytest -q tests/unit/test_public_release_docs.py` reported
    `78 passed`; `uv run pytest -q tests/unit/test_release_versions.py
    tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
    reported `108 passed`; strict OpenSpec validation for
    `require-beta-candidate-validation` was valid; main specs reported
    `30 passed, 0 failed`; Ruff and whitespace passed.
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `109 passed`; covers the release-version, public-release docs, and
    Kubernetes version policy slice after refreshing the full preflight evidence.
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `107 passed`; covers the final approval preflight's
    release-version regression, public release docs, and Kubernetes version
    policy slice.
- Staged replacement prerelease notes refresh on 2026-06-14T05:14:50Z
  - Result: the paste-ready prerelease body now names the read-only
    public-release preflight, drift scan, local artifact proof, and runtime
    proof, and public-release docs tests pin the current `79 passed`,
    `109 passed`, and `08:38` full-preflight evidence instead of older
    intermediate counts.
  - Latest refresh on 2026-06-14T07:10:20Z: the staged notes body now rejects
    the stale `04:51:13Z`, `77 passed`, `107 passed`, and `79 passed`
    focused-evidence bullets while keeping the latest live snapshot and local
    artifact proof.
  - Latest refresh after the 2026-06-14T08:50:46Z standalone live snapshot: the
    paste-ready PR draft regression now pins the shared latest live snapshot
    timestamp and the hardened release-title/public-URL/published-timestamp
    release-state evidence instead of accepting the older `06:36:14Z` snapshot
    as current.
  - Latest full-preflight refresh on 2026-06-14T08:38:25Z: the paste-ready
    prerelease body now carries the fresh `preflightAt=2026-06-14T08:38:25Z`,
    `snapshotAt=2026-06-14T08:38:26Z`,
    `localArtifactProofAt=2026-06-14T08:38:29Z`, `79 passed`, and `109 passed`
    evidence while preserving the 08:34 standalone snapshot as historical proof.
- `uv run pytest -q tests/unit/test_guard_beta_release.py`
  - Result: `10 passed in 2.38s`; covers beta release guard evidence parsing,
    including mutually exclusive live upstream/account smoke checklist choices.
- `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3 --require-channel beta`
  - Result: tag/version mapping passed with `channel=beta` and
    `pypi_version=1.20.0b3`.
- `npx --yes @fission-ai/openspec@latest validate --specs`
  - Result: all specs valid (`30 passed, 0 failed`).
- `uvx ruff format --check tests/unit/test_public_release_docs.py && uvx ruff check tests/unit/test_public_release_docs.py && git diff --check`
  - Result: `1 file already formatted`; `All checks passed!`;
    `git diff --check` clean.
- `uvx ruff format --check scripts/guard_beta_release.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py`
  - Result: `3 files already formatted`
- `uvx ruff check scripts/guard_beta_release.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py`
  - Result: `All checks passed!`
- `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict`
  - Result: `Change 'require-beta-candidate-validation' is valid` after adding
    the contradictory live-smoke evidence scenario.
- `git diff --check`
  - Result: clean
- `rg -n "helm (install|upgrade) agent-lb oci://ghcr\.io/aneym/charts/agent-lb|--version 1\.20\.0-beta\.3|--devel" README.md deploy/helm/agent-lb/README.md`
  - Result: every public Helm OCI install/upgrade command in README/Helm docs
    is paired with the beta version and `--devel`.
- `rg -n "ghcr\.io/aneym/agent-lb:latest|uvx agent-lb" README.md deploy/helm/agent-lb/README.md`
  - Result: no matches; public beta docs no longer point at an unpublished
    `latest` image tag or bare `uvx agent-lb` install.
- `bash scripts/install-service.sh --print | rg 'com\.aneyman\.agent-lb|com\.agent-lb'`
  - Result: generated plist contains `com.aneyman.agent-lb`
- `npx --yes @fission-ai/openspec@latest validate macos-menubar-app --strict`
  - Result: `Change 'macos-menubar-app' is valid`
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make package`
  - Result: rerun after public README/service-label/runtime-release,
    package summary alignment, and OpenClaw metadata edits passed; frontend
    production build passed, import smoke passed, Hatch built the wheel from
    the sdist, and `scripts/verify-wheel-assets.py` passed
- `uvx --from twine==6.2.0 twine check dist/*`
  - Result: wheel and sdist passed metadata validation
- `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3`
  - Result: tag/version mapping passed (`pypi_version=1.20.0b3`)
- `unzip -p dist/agent_lb-1.20.0b3-py3-none-any.whl agent_lb-1.20.0b3.dist-info/METADATA | rg -n '^(Name|Version|Summary|Author|Maintainer|Maintainer-email|Keywords|Classifier):'`
  - Result: wheel metadata includes `Name: agent-lb`, `Version: 1.20.0b3`,
    `Summary: ChatGPT and Claude account load balancer & proxy with usage
    tracking, dashboard, and OpenAI/Anthropic-compatible endpoints`,
    `Maintainer: Alex Neyman`, and
    OpenAI/ChatGPT/Claude/Anthropic/OpenCode/OpenClaw keywords, plus
    `Classifier: Development Status :: 4 - Beta`
- `npx --yes @fission-ai/openspec@latest validate fix-runtime-release-repository --strict`
  - Result: `Change 'fix-runtime-release-repository' is valid`
- `uv run pytest -q tests/unit/test_runtime_version.py tests/integration/test_runtime_api.py`
  - Result: `11 passed in 0.27s`
- `rg -n 'Soju06/agent-lb/releases/latest|api\.github\.com/repos/Soju06/agent-lb/releases/latest' app clients frontend README.md GETTING-STARTED.md`
  - Result: no source/runtime docs matches; later `GOAL.md`, `HANDOFF.md`,
    OpenSpec task notes, and public-release docs tests intentionally record the
    stale live-daemon observation.
- `curl -fsS http://127.0.0.1:2455/health`
  - Result: live launchd service returned `{"status":"ok"}`
- `curl -fsS http://127.0.0.1:2455/api/runtime/version`
  - Result: live launchd service is on `currentVersion` `1.20.0-beta.3` but its
    release link is stale until approved restart/reinstall; it returned
    `releaseUrl` `https://github.com/Soju06/agent-lb/releases/latest` with
    response `checkedAt` `2026-06-14T04:35:55.088884Z` during a read-only check
    at `2026-06-14T04:36:45Z` even though the candidate source/tests point to
    `https://github.com/aneym/agent-lb/releases/latest`
  - No restart was performed because the daemon is healthy and service restarts
    are approval-gated.
- `npx --yes @fission-ai/openspec@latest validate --specs`
  - Result: all specs valid (`30 passed, 0 failed`) after replacing archived
    OpenSpec `Purpose` placeholders
- Placeholder scan across `openspec/specs`
  - Result: no archive-generated purpose placeholders remain
- Placeholder scan across `.agents/commands/opsx/sync.md`,
  `.agents/skills/openspec-sync-specs/SKILL.md`, and `openspec/specs`
  - Result: no placeholders remain after tightening the opsx sync command and
    matching skill wording
- `uv run pytest -q tests/integration/test_accounts_api.py tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py tests/unit/test_accounts_service_transitions.py`
  - Result: `48 passed in 4.92s`; covers account API subscription status
    normalization plus dashboard/report/account transition subscription paths
- `uv run pytest -q tests/unit/test_trusted_proxy_client_ip.py tests/integration/test_trusted_proxy_auth.py tests/unit/test_runtime_version.py tests/integration/test_runtime_api.py`
  - Result: `26 passed in 0.87s`
- `npx --yes @fission-ai/openspec@latest validate harden-trusted-proxy-api-key-auth --strict`
  - Result: `Change 'harden-trusted-proxy-api-key-auth' is valid`
- `npx --yes @fission-ai/openspec@latest validate fix-runtime-release-repository --strict`
  - Result: `Change 'fix-runtime-release-repository' is valid`
- `uvx ruff format --check . && uvx ruff check . && git diff --check`
  - Result: `661 files already formatted`; `All checks passed!`;
    `git diff --check` clean after the latest release-risk audit edits
- `uv run pytest -q tests/integration/test_anthropic_proxy.py tests/integration/test_http_responses_bridge.py tests/integration/test_proxy_sticky_sessions.py tests/integration/test_migrations.py tests/unit/test_db_migrate.py tests/integration/test_settings_api.py tests/unit/test_claude_lb_launch.py tests/unit/test_proxy_load_balancer_refresh.py tests/integration/test_accounts_api.py tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py tests/integration/test_public_usage.py tests/unit/test_accounts_service_transitions.py`
  - Result: `290 passed, 6 skipped, 2 warnings in 34.54s`; covers Anthropic
    quota diagnostics, HTTP bridge compatibility, proxy sticky sessions,
    migration/provider-default compatibility, account subscription visibility
    and checks, dashboard/report/public usage, settings, and launcher
    formatting paths
- `cd frontend && bun run test src/features/accounts/components/account-detail.test.tsx src/features/accounts/components/accounts-page.test.tsx src/features/accounts/hooks/use-accounts.test.ts src/features/accounts/schemas.test.ts src/test/mocks/handler-coverage.test.ts src/__integration__/accounts-flow.test.tsx`
  - Result: `6 passed (6)` test files, `21 passed (21)` tests; covers account
    subscription UI, hooks, schemas, mocks, and flow coverage
- `cd clients/macos-menubar && swift test --filter 'AccountFilterTests|ModelDecodingTests|ServiceControllerTests'`
  - Result: `43 tests, 0 failures`; covers account filtering, subscription
    ledger decoding, and service-controller label/runtime status paths
- Live public-state refresh on 2026-06-14 at 2026-06-14T05:18:11Z:
  `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3`
  - Result: the helper printed `snapshotAt=2026-06-14T05:18:11Z`; open PRs
    remain `[]`; the only visible release remains prerelease
    `v1.20.0-beta.3`, created `2026-06-11T19:57:43Z` and published
    `2026-06-11T19:57:52Z`; it is not a draft, not latest, not immutable, and
    release assets remain `[]`. Hosted repo description/homepage/topics are
    still stale or empty, recent branch workflow runs remain `[]`, PyPI
    `agent-lb` remains unavailable, and GHCR
    `ghcr.io/aneym/agent-lb:1.20.0-beta.3`, `ghcr.io/aneym/agent-lb:beta`,
    and `ghcr.io/aneym/charts/agent-lb:1.20.0-beta.3` manifests remain
    denied/not visible. The existing prerelease body is still the older
    pricing/warmup beta body and still contains a stale migration caveat that
    the current local migration evidence supersedes.
  - Refresh: the one-command approval preflight reran this helper on
    2026-06-14T05:51:19Z and printed
    `snapshotAt=2026-06-14T05:51:19Z`; open PRs and recent branch workflow runs
    still returned `[]`, the prerelease still has no assets, PyPI remains 404,
    and the GHCR image/chart manifests remain denied/not visible.
  - Refresh on 2026-06-14T05:55:14Z printed
    `snapshotAt=2026-06-14T05:55:14Z`; open PRs and recent branch workflow runs
    still returned `[]`, the existing prerelease still has no assets and the
    older pricing/warmup body, hosted repo metadata is still stale/empty, PyPI
    remains 404, and the GHCR image/chart manifests remain denied/not visible.
  - Refresh on 2026-06-14T05:59:27Z printed
    `snapshotAt=2026-06-14T05:59:27Z` and `snapshotOptionalFailures=5`; open
    PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no assets and the older pricing/warmup body, hosted
    repo metadata is still stale/empty, PyPI remains 404, and the GHCR
    image/chart manifests remain denied/not visible.
  - Refresh on 2026-06-14T06:03:30Z printed
    `snapshotAt=2026-06-14T06:03:30Z` and `snapshotOptionalFailures=5`; open
    PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no assets and the older pricing/warmup body, hosted
    repo metadata is still stale/empty, PyPI remains 404, and the GHCR
    image/chart manifests remain denied/not visible.
  - Refresh on 2026-06-14T06:09:45Z printed
    `snapshotAt=2026-06-14T06:09:45Z`, `snapshotOptionalFailures=5`, and
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no assets and the older pricing/warmup body, hosted
    repo metadata is still stale/empty, PyPI remains 404, and the same named
    public artifact probes remain missing.
  - Refresh inside the full preflight on 2026-06-14T06:11:16Z printed
    `snapshotAt=2026-06-14T06:11:16Z`, `snapshotOptionalFailures=5`, and
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no assets and the older pricing/warmup body, hosted
    repo metadata is still stale/empty, PyPI remains 404, and the same named
    public artifact probes remain missing.
  - Refresh inside the full preflight on 2026-06-14T06:16:56Z printed
    `snapshotAt=2026-06-14T06:16:56Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    and
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no assets and the older pricing/warmup body, hosted
    repo metadata is still stale/empty, PyPI remains 404, and the same named
    public artifact probes remain missing.
  - Refresh inside the full preflight on 2026-06-14T06:25:33Z printed
    `snapshotAt=2026-06-14T06:25:33Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    and
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no assets and the older pricing/warmup body, hosted
    repo metadata is still stale/empty, PyPI remains 404, and the same named
    public artifact probes remain missing.
  - Refresh inside the full preflight on 2026-06-14T07:03:24Z printed
    `snapshotAt=2026-06-14T07:03:24Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    and
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no assets and the older pricing/warmup body, hosted
    repo metadata is still stale/empty, PyPI remains 404, and the same named
    public artifact probes remain missing.
  - Latest read-only refresh inside the full preflight on 2026-06-14T08:38:26Z
    printed `snapshotAt=2026-06-14T08:38:26Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    and
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the existing
    prerelease still has no exact wheel/sdist assets and the older pricing/warmup
    body, hosted repo metadata is still stale/empty, PyPI remains 404, and the
    same named public artifact probes remain missing. The PyPI JSON probe now
    checks for the expected version, summary, project URLs, and exact wheel/sdist
    filenames once PyPI becomes visible. The selected tag, release
    title, public release URL, published timestamp, prerelease flag, draft
    status, public visibility, private/archive state, and default branch checks
    passed. No GitHub mutations were made.
  - Latest standalone read-only refresh on 2026-06-14T08:34:06Z printed
    `snapshotAt=2026-06-14T08:34:06Z`,
    `snapshotOptionalFailures=5`, and the same blocker names; open PRs/runs
    remained `[]`, the existing prerelease still has no assets and the older
    pricing/warmup body, hosted repo metadata is still stale/empty, PyPI remains
    404, pip index still has no matching distribution, and GHCR image/chart
    manifests remain denied/not visible. No GitHub mutations were made.
  - Latest standalone read-only refresh on 2026-06-14T08:50:46Z printed
    `snapshotAt=2026-06-14T08:50:46Z`,
    `snapshotOptionalFailures=5`, and the same blocker names; open PRs/runs
    remained `[]`, the existing prerelease still has no assets and the older
    pricing/warmup body, hosted repo metadata is still stale/empty, PyPI remains
    404, pip index still has no matching distribution, and GHCR image/chart
    manifests remain denied/not visible. No GitHub mutations were made.
  - 2026-06-14T08:32:28Z verification after aligning the paste-ready PR draft,
    goal brief, and release-publication blockers to the 08:27 standalone
    snapshot: public-release docs tests reported `79 passed`, the
    release-version/public-docs/K8s slice reported `109 passed`,
    `require-beta-candidate-validation` strict validation passed, main specs
    reported `30 passed, 0 failed`, Ruff was clean, and whitespace was clean.
  - 2026-06-14T08:36:27Z verification after refreshing the tested live snapshot
    and publish-readiness evidence to 08:34: public-release docs tests reported
    `79 passed`, the release-version/public-docs/K8s slice reported
    `109 passed`, `require-beta-candidate-validation` strict validation passed,
    main specs reported `30 passed, 0 failed`, Ruff was clean, and whitespace
    was clean.
  - 2026-06-14T08:42:42Z verification after refreshing the full preflight
    evidence to 08:38: public-release docs tests reported `79 passed`, the
    release-version/public-docs/K8s slice reported `109 passed`,
    `require-beta-candidate-validation` strict validation passed, main specs
    reported `30 passed, 0 failed`, Ruff was clean, and whitespace was clean.
  - 2026-06-14T08:45:50Z continuation guardrail: public-release docs tests now
    pin the `HANDOFF.md#known-remaining-risk` section so the release cannot be
    described as complete while commit/PR, public metadata, package/container
    assets, runtime restart, and publication actions remain approval-gated.
    `uv run pytest -q tests/unit/test_public_release_docs.py` reported
    `80 passed`. No GitHub mutations were made.
  - 2026-06-14T08:48:06Z OpenSpec completion-boundary hardening: the active
    release-management spec now requires the handoff to keep remaining-risk and
    completion status explicit until commit/PR, PR-head CI/Codex review, public
    metadata, package/container/chart artifacts, release assets/body, runtime
    restart/reinstall, and publication proof are complete or accepted. Public
    release docs tests now pin that normative contract. 2026-06-14T08:48:54Z
    verification: public-release docs tests reported `80 passed`; the
    release-version/public-docs/K8s slice reported `110 passed`;
    `require-beta-candidate-validation` strict validation passed; Ruff was
    clean; whitespace was clean. No GitHub mutations were made.
  - 2026-06-14T08:54:03Z verification after refreshing the 08:50 live/readiness
    evidence: public-release docs tests reported `80 passed`; the
    release-version/public-docs/K8s slice reported `110 passed`;
    `require-beta-candidate-validation` strict validation passed; Ruff was
    clean; whitespace was clean. No GitHub mutations were made.
  - 2026-06-14T07:12:55Z focused verification after the live-snapshot evidence
    split: public-release docs tests reported `78 passed`, release-version/public
    docs/K8s tests reported `108 passed`, beta guard reported `10 passed`,
    `require-beta-candidate-validation` strict validation passed, main specs
    reported `30 passed, 0 failed`, Ruff was clean, and whitespace was clean.
- Release tag/dirty-tree preflight on 2026-06-14 at 2026-06-14T04:52:56Z:
  - Result: `v1.20.0-beta.3^{}` peels to
    `b00efd4fce34f42edb455a78b9cf34df8600e337`, matching current `HEAD`;
    `git status --porcelain | wc -l` reported `166` dirty/untracked paths.
    Dispatching a release workflow for this tag would build committed tag
    contents only and omit the current dirty release-readiness overlay until
    those changes are committed and tagged.
  - Current refresh: `git status --short | wc -l` reported `167`
    dirty/untracked paths after the live-snapshot preflight wiring.
- `git diff --check`, `uvx ruff format --check .`, and `uvx ruff check .`
  after adding release-tag guardrails
  - Result: diff whitespace check clean; `661 files already formatted`; `All
    checks passed!`
- `uvx ruff format --check .`, `uvx ruff check .`, and `git diff --check`
  after adding public beta install-doc regressions
  - Result: `661 files already formatted`; `All checks passed!`;
    `git diff --check` clean.
- `uvx ruff format --check tests/unit/test_public_release_docs.py`,
  `uvx ruff check tests/unit/test_public_release_docs.py`, and
  `git diff --check` after adding GitHub intake/security release-train and
  provider-positioning regressions
  - Result: `1 file already formatted`; `All checks passed!`;
    `git diff --check` clean.
- `uvx ruff format --check tests/unit/test_public_release_docs.py`,
  `uvx ruff check tests/unit/test_public_release_docs.py`, and
  `git diff --check` after tightening PR-template screenshot proof
  - Result: `1 file already formatted`; `All checks passed!`;
    `git diff --check` clean.
- `uvx ruff format --check tests/unit/test_public_release_docs.py`,
  `uvx ruff check tests/unit/test_public_release_docs.py`, and
  `git diff --check` after adding the beta-publish workflow guard
  - Result: `1 file already formatted`; `All checks passed!`;
    `git diff --check` clean.
- `rg -n "1\.16\.0|1\.17\.0|Codex/ChatGPT|ОpenCode|com\.agent-lb|ghcr\.io/aneym/agent-lb:latest" .github README.md GETTING-STARTED.md AGENTS.md deploy/helm/agent-lb/README.md pyproject.toml`
  - Result: no matches after the final public-intake template alignment.
- `uv lock --locked`
  - Result: current trimmed lockfile is accepted (`Resolved 120 packages in
    3ms`); `uv.lock` retains only the intended `agent-lb` version normalization
    diff.
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `113 passed in 4.23s`; covers release version normalization,
    beta-guard branch/evidence checks, public package metadata URLs, public docs
    guardrails, and Kubernetes version policy.
- `uv run pytest -q tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `86 passed in 3.48s`; covers beta publish guards plus public docs,
    including the existing-prerelease dispatch path and tag-scoped release
    workflow concurrency, plus the PR/contributor public client/onboarding sync
    gate.
- `uv run pytest -q tests/unit/test_guard_beta_release.py tests/unit/test_cleanup_superseded_beta_prs.py tests/unit/test_sync_codex_ok_labels.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `99 passed in 3.75s`; covers beta publish guards, superseded-beta
    cleanup, Codex-label sync, public docs, git workflow fork policy, and
    Kubernetes version policy.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `76 passed in 0.70s`; additionally covers the
    runtime-portability Codex session retag Docker examples pinning the current
    prerelease image instead of `latest`.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_sync_codex_ok_labels.py`
  - Result: `47 passed in 0.20s`; additionally covers GitHub automation
    defaults for the public fork, including all-contributors and Codex review
    label sync scripts.
- `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `39 passed in 0.21s`; additionally covers exact GitHub topic
    replacement in the approval packet by parsing the staged topics API payload
    and comparing it to the README metadata topic list, plus the replacement
    prerelease notes draft without the obsolete SQLite migration caveat, and
    fail-closed post-publish proof script plus expanded command contract for
    PyPI, pip-index, GHCR image tags, the Helm chart package, GitHub repository
    metadata, GitHub release assets, and the replacement release body.
- `rg -n 'ghcr\.io/aneym/agent-lb:latest' openspec/specs README.md GETTING-STARTED.md deploy/helm/agent-lb/README.md pyproject.toml .github --glob '!tests/**'`
  - Result: no public docs, OpenSpec specs, Helm docs, package metadata, or
    workflow matches for the unpublished Docker `latest` tag.
- `rg -n 'Soju06/agent-lb' .github/scripts/check_all_contributors.py .github/scripts/sync_codex_ok_labels.py tests/unit/test_public_release_docs.py`
  - Result: no stale upstream owner references in the GitHub automation scripts;
    the only matches are the negative public-release regression assertions.
- ``rg -n 'Soju06/agent-lb|upstream `aneym/agent-lb`' tests/unit/test_guard_beta_release.py tests/unit/test_cleanup_superseded_beta_prs.py tests/unit/test_sync_codex_ok_labels.py .agents/conventions/git-workflow.md AGENTS.md``
  - Result: no matches after aligning release automation fixtures and agent git
    workflow policy with the public fork.
- `bash -n scripts/public-release-drift-scan.sh scripts/public-release-preflight.sh && ./scripts/public-release-drift-scan.sh`
  on 2026-06-14T04:24:37Z
  - Result: passed; no unpublished Docker `latest` install shortcuts, stale
    upstream runtime release URL, deleted public screenshot artifact references,
    retired fork names, or stale hosted-repo description text remain in the
    scanned public release surfaces.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  after recording the final public-surface scan evidence and refreshing the
  live blocker snapshot, plus adding the paste-ready PR draft guard:
  `60 passed`.
- `uvx ruff format --check tests/unit/test_public_release_docs.py` and
  `uvx ruff check tests/unit/test_public_release_docs.py`
  after recording the final public-surface scan evidence and refreshing the
  live blocker snapshot: `1 file already formatted`; `All checks passed!`.
- Paste-ready PR draft cleanup on 2026-06-14T05:46:47Z:
  the staged handoff PR draft no longer contains a `Fill this in` placeholder
  for related issue/discussion. `uv run pytest -q
  tests/unit/test_public_release_docs.py` -> `76 passed`; Ruff format/check for
  `tests/unit/test_public_release_docs.py` passed; `git diff --check` passed.
- `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict`
  after refreshing the pending PR-head CI/Codex-review task evidence:
  `Change 'require-beta-candidate-validation' is valid`.
- `uvx ruff format --check tests/unit/test_guard_beta_release.py tests/unit/test_cleanup_superseded_beta_prs.py tests/unit/test_sync_codex_ok_labels.py tests/unit/test_public_release_docs.py`
  - Result: `4 files already formatted`
- `uvx ruff check tests/unit/test_guard_beta_release.py tests/unit/test_cleanup_superseded_beta_prs.py tests/unit/test_sync_codex_ok_labels.py tests/unit/test_public_release_docs.py`
  - Result: `All checks passed!`
- `uvx ruff format --check scripts/release_versions.py scripts/guard_beta_release.py tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py`
  - Result: `5 files already formatted`
- `uvx ruff check scripts/release_versions.py scripts/guard_beta_release.py tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py`
  - Result: `All checks passed!`
- `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3 --require-channel beta`
  - Result: tag/version mapping passed with `channel=beta` and
    `pypi_version=1.20.0b3`.
- `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict`
  - Result: `Change 'require-beta-candidate-validation' is valid`
- 2026-06-14T07:34:03Z post-publish proof identity hardening refresh:
  `bash -n scripts/public-release-postpublish-proof.sh` passed;
  `uv run pytest -q tests/unit/test_public_release_docs.py` -> `78 passed`;
  `uv run pytest -q tests/unit/test_release_versions.py
  tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `108 passed`; strict validation for
  `require-beta-candidate-validation` was valid; main specs -> `30 passed, 0
  failed`; Ruff and whitespace passed.
- `npx --yes @fission-ai/openspec@latest validate --specs`
  - Result: all specs valid (`30 passed, 0 failed`) after documenting
    PEP 440-normalized `uv.lock` prerelease spelling.
- OpenSpec stale-validation cleanup on 2026-06-14:
  `add-claude-fable-pricing`, `fix-token-invalidated-account-state`,
  `add-reports-page`, `surface-anthropic-session-route-errors`,
  `add-auth-guardian-refresh`, `add-fill-first-routing-strategy`,
  `create-pytest-required-check-placeholders`, and
  `rate-limit-aware-retry-and-resume` now carry current validation evidence in
  their task ledgers. Strict validation is green for each targeted change after
  normalizing requirement headers/bodies where the current CLI demanded
  explicit `MUST`/`SHALL` lines.
- Additional active-change validation cleanup on 2026-06-14T01:55:40Z:
  `add-account-subscription-ledger`, `fix-public-usage-window-backfill`, and
  `restore-codex-image-generation-tool` now carry current strict validation
  evidence from `npx --yes @fission-ai/openspec@latest`; active task and verify
  reports no longer contain stale OpenSpec CLI-unavailable deferrals.
- Active OpenSpec full-sweep repair on 2026-06-14T02:03:58Z:
  `decompose-proxy-service` now carries a `proxy-service-architecture` spec
  delta for the stable proxy facade, private `_service` domains, and
  architecture ratchets. `npx --yes @fission-ai/openspec@latest validate
  decompose-proxy-service --strict` passed, and a strict loop over all active
  changes reported `validated 54 active changes`.
- Approval-preflight active OpenSpec script on 2026-06-14T02:13:06Z:
  `./scripts/validate-active-openspec-changes.sh`
  - Result: `validated 54 active changes`.
- PR-head gate evidence refresh on 2026-06-14T07:20:58Z:
  `gh pr list --repo aneym/agent-lb --state open` and
  `gh run list --repo aneym/agent-lb --branch main --limit 10`; dirty-count
  check used `git status --porcelain | wc -l`.
  - Result: both returned `[]`; the remaining active unchecked OpenSpec tasks
    are still blocked on a committed PR head with CI/Codex-review evidence,
    and the working tree still has `167` dirty/untracked paths.
- PR-head proof helper dry-run on 2026-06-14T05:40:38Z:
  `./scripts/public-release-pr-head-proof.sh 0`
  - Result: expected `exit_status=1` after printing
    `prHeadProofAt=2026-06-14T05:40:38Z`; `gh pr view 0 --repo
    aneym/agent-lb` reported `no pull requests found`, so real PR-head proof
    remains pending until a release PR exists.
- PR-head proof helper SHA-identity hardening on 2026-06-14T08:17:46Z:
  `./scripts/public-release-pr-head-proof.sh 0`
  - Result: expected `exit_status=1` after printing
    `prHeadProofAt=2026-06-14T08:17:46Z`; `gh pr view 0 --repo
    aneym/agent-lb` still reported `no pull requests found`. Successful
    PR-head proofs now print `pr_head_sha` and `pr_head_short`, then require
    the current-head Codex classifier output to include the same
    `head=${pr_head_short}` fragment before treating CI/Codex-review evidence
    as matched to the proved commit.
- PR-head proof timestamp regression sweep on 2026-06-14T05:42:14Z:
  `uv run pytest -q tests/unit/test_public_release_docs.py`,
  `uv run pytest -q tests/unit/test_release_versions.py
  tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`,
  `npx --yes @fission-ai/openspec@latest validate
  require-beta-candidate-validation --strict`, `npx --yes
  @fission-ai/openspec@latest validate create-pytest-required-check-placeholders
  --strict`, `npx --yes @fission-ai/openspec@latest validate --specs`, Ruff
  format/check for `tests/unit/test_public_release_docs.py`, and `git diff
  --check`.
  - Result: `76 passed`, `106 passed`, both active changes valid, main specs
    `30 passed, 0 failed`, Ruff clean, whitespace clean; dirty tree still has
    `167` paths.
- PR-head proof SHA-identity verification on 2026-06-14T08:18:57Z:
  shell syntax passed; `uv run pytest -q tests/unit/test_public_release_docs.py`
  reported `78 passed`; the release-version/public-doc/K8s slice reported
  `108 passed`; `require-beta-candidate-validation` strict validation was
  valid; main specs reported `30 passed, 0 failed`; Ruff and whitespace were
  clean.
- Active OpenSpec unchecked-task, issue-chooser, runtime-daemon, and public
  markdown-fence regression on 2026-06-14T04:07:00Z:
  `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `73 passed`; covers the rule that active unchecked OpenSpec tasks
    must be exactly the PR-head CI/Codex-review gates and must record live PR
    and CI-run evidence, names the PR-head proof helper, records the healthy
    but stale launchd runtime boundary, and pins public issue-chooser routing,
    balanced public markdown code fences, and deterministic screenshot capture
    against a repo-owned preview server.
- PR template and contributor release-proof regression on
  2026-06-14T03:42:05Z:
  `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `73 passed`; covers approval-gated release/package/publication PR
    guidance requiring the live blocker snapshot, PR-head proof, publish
    readiness, and post-publish proof commands. Strict OpenSpec validation for
    `require-beta-candidate-validation` was valid, Ruff format/check passed for
    `tests/unit/test_public_release_docs.py`, and `git diff --check` passed.
- Public release preflight script on 2026-06-14T04:51:13Z:
  `./scripts/public-release-preflight.sh v1.20.0-beta.3`
  - Result: passed; included `gh pr list` -> `[]`, `gh run list` -> `[]`,
    `uv lock --locked`, release helper `bash -n` syntax checks,
    public release drift scan, local artifact proof, `105 passed`
    for release-version/public-doc/Kubernetes tests, `10 passed` for beta
    guard, release verifier `channel=beta` and `pypi_version=1.20.0b3`,
    `validated 54 active changes`, `30 passed, 0 failed` for main specs, Ruff,
    and `git diff --check`.
- Runtime proof on 2026-06-14T05:35:44Z:
  `./scripts/public-release-runtime-proof.sh v1.20.0-beta.3`
  - Result: expected non-zero before approved restart/reinstall (`rc=1`);
    the helper printed `runtimeProofAt=2026-06-14T05:35:44Z`, release
    tag/version metadata parsed, `http://127.0.0.1:2455/health` returned
    `true` for `.status == "ok"`, and the
    `/api/runtime/version` assertion returned `false` because the healthy
    daemon still serves `https://github.com/Soju06/agent-lb/releases/latest`
    instead of `https://github.com/aneym/agent-lb/releases/latest`.
  - Refresh on 2026-06-14T05:55:14Z: the helper printed
    `runtimeProofAt=2026-06-14T05:55:14Z` and exited expected non-zero (`rc=1`);
    health still passed, and the `/api/runtime/version` assertion still returned
    `false` because the healthy daemon has not been restarted/reinstalled from
    the candidate.
  - Refresh on 2026-06-14T06:21:57Z: the helper printed
    `runtimeProofAt=2026-06-14T06:21:57Z` and exited expected non-zero (`rc=1`);
    health still passed, and the `/api/runtime/version` assertion still returned
    `false` because the healthy daemon has not been restarted/reinstalled from
    the candidate.
  - Refresh on 2026-06-14T07:19:03Z: the helper printed
    `runtimeProofAt=2026-06-14T07:19:03Z` and exited expected non-zero (`rc=1`);
    release metadata parsed, health still passed, and the `/api/runtime/version`
    assertion still returned `false` because the healthy daemon has not been
    restarted/reinstalled from the candidate.
- Approval-packet local artifact proof alignment on 2026-06-14T05:00:08Z:
  - Result: `HANDOFF.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and
    `.github/CONTRIBUTING.md` now name
    `./scripts/public-release-local-artifact-proof.sh <approved-release-tag>`
    alongside the live snapshot, PR-head, publish-readiness, and post-publish
    proof commands. `uv run pytest -q tests/unit/test_public_release_docs.py`
    passed with `75 passed`, the release/docs/Kubernetes slice passed with
    `105 passed`, and `./scripts/public-release-preflight.sh v1.20.0-beta.3`
    passed end to end after the approval-packet update.
- Local artifact proof on 2026-06-14T05:35:44Z:
  `./scripts/public-release-local-artifact-proof.sh v1.20.0-beta.3`
  - Result: passed after printing
    `localArtifactProofAt=2026-06-14T05:35:44Z`; derived `channel=beta` and
    `pypi_version=1.20.0b3`, found the matching wheel and sdist, confirmed the
    sdist README hash matches the repository README, found no dev-only
    top-level sdist paths, verified wheel metadata including
    `Maintainer: Alex Neyman` and the beta classifier,
    confirmed README image references include
    `ghcr.io/aneym/agent-lb:1.20.0-beta.3`, and `twine check` passed for both
    local artifacts.
- Focused release-doc proof on 2026-06-14T05:14:50Z:
  `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `76 passed`; covers the runtime proof helper in addition to the
    approval preflight, live snapshot, publish-readiness, post-publish proof,
    PR-head proof, local artifact proof, drift-scan, screenshot, and public
    metadata gates.
- Focused release/docs/Kubernetes proof on 2026-06-14T05:14:50Z:
  `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `106 passed`.
- Focused public-docs/Kubernetes proof on 2026-06-14T05:14:50Z:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  - Result: `79 passed`.
- Publish-readiness guard on 2026-06-14T04:52:56Z:
  `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3`
  - Result: expected non-zero before publication; the tag matched `HEAD`, then
    the guard reported the working tree is dirty with 166 dirty/untracked
    paths.
  - Dirty-count refresh on 2026-06-14T05:35:44Z: the guard printed
    `publishReadinessAt=2026-06-14T05:35:44Z`, confirmed the approved tag still
    points at `HEAD`, verified `channel=beta` and `pypi_version=1.20.0b3`, then
    printed `dirty_count=167`, listed the current 167 dirty/untracked paths,
    and exited non-zero before any publication command.
  - Dirty-count refresh on 2026-06-14T05:55:14Z: the guard printed
    `publishReadinessAt=2026-06-14T05:55:14Z`, confirmed the approved tag still
    points at `HEAD`, verified `channel=beta` and `pypi_version=1.20.0b3`, then
    printed `dirty_count=167`, listed the current 167 dirty/untracked paths, and
    exited non-zero before any publication command.
  - Dirty-count refresh on 2026-06-14T06:21:30Z: the guard printed
    `publishReadinessAt=2026-06-14T06:21:30Z`, confirmed the approved tag still
    points at `HEAD`, verified `channel=beta` and `pypi_version=1.20.0b3`, then
    printed `dirty_count=167`, listed the current 167 dirty/untracked paths, and
    exited non-zero before any publication command.
  - Dirty-count refresh on 2026-06-14T07:13:20Z: the guard printed
    `publishReadinessAt=2026-06-14T07:13:20Z`, confirmed the approved tag still
    points at `HEAD`
    (`b00efd4fce34f42edb455a78b9cf34df8600e337`), verified `channel=beta` and
    `pypi_version=1.20.0b3`, then printed `dirty_count=167`, listed the current
    167 dirty/untracked paths, and exited non-zero before any publication
    command.
  - Hardened dirty-count refresh on 2026-06-14T07:29:58Z: the guard printed
    `publishReadinessAt=2026-06-14T07:29:58Z`, confirmed the approved tag still
    points at `HEAD`
    (`b00efd4fce34f42edb455a78b9cf34df8600e337`), verified `channel=beta` and
    `pypi_version=1.20.0b3`, then printed `dirty_count=167`, listed the current
    167 dirty/untracked paths, and exited expected non-zero before any
    publication command or live PR/run probe.
  - Latest local-main refresh on 2026-06-14T08:34:12Z: the guard printed
    `publishReadinessAt=2026-06-14T08:34:12Z`, confirmed
    `current_branch=main`, confirmed the approved tag, `HEAD`, and `main_sha`
    are all `b00efd4fce34f42edb455a78b9cf34df8600e337`, verified
    `channel=beta` and `pypi_version=1.20.0b3`, then printed
    `dirty_count=167`, listed the current 167 dirty/untracked paths, and exited
    expected non-zero before any publication command or live PR/run probe.
  - Latest local-main refresh on 2026-06-14T08:50:46Z: the guard printed
    `publishReadinessAt=2026-06-14T08:50:46Z`, confirmed
    `current_branch=main`, confirmed the approved tag, `HEAD`, and `main_sha`
    are all `b00efd4fce34f42edb455a78b9cf34df8600e337`, verified
    `channel=beta` and `pypi_version=1.20.0b3`, then printed
    `dirty_count=167`, listed the current 167 dirty/untracked paths, and exited
    expected non-zero before any publication command or live PR/run probe.
  - Clean-path behavior is now pinned: after tag, version/channel, clean-tree,
    open-PR, and current-head main-run probes succeed, the guard prints
    `publish readiness passed at ${PUBLISH_READINESS_AT}`. The guard now also
    prints `current_branch`, `main_sha`, `open_pr_count`, and
    `current_head_main_run_count`, then fails closed if the checkout is not
    local `main` at `HEAD`, if no returned `main` workflow run targets the
    current `HEAD`, or if any returned current-head run is not completed with a
    success, skipped, or neutral conclusion.
  - 2026-06-14T08:16:23Z local-main guard hardening verification: shell syntax
    passed; `uv run pytest -q tests/unit/test_public_release_docs.py` reported
    `78 passed`; the release-version/public-doc/K8s slice reported
    `108 passed`; `require-beta-candidate-validation` strict validation was
    valid; main specs reported `30 passed, 0 failed`; Ruff and whitespace were
    clean.
- Post-publish artifact proof script on 2026-06-14T04:36:34Z:
  `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3`
  - Result: expected non-zero before publication (`rc=1`); release metadata
    resolved to `channel=beta` and `pypi_version=1.20.0b3`, then PyPI JSON
    returned 404. Use after approval to prove PyPI, pip-index, GHCR
    image/chart manifests, GitHub repository metadata, GitHub release assets,
    and replacement release body freshness.
- Post-publish proof scope refresh on 2026-06-14T05:35:44Z:
  `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3`
  - Result: expected non-zero before publication (`rc=1`); the helper printed
    `postpublishProofAt=2026-06-14T05:35:44Z`, release metadata resolved to
    `channel=beta` and `pypi_version=1.20.0b3`, then PyPI JSON returned 404
    before the later GHCR image/chart, GitHub repository metadata,
    release-asset, and release-body checks.
  - `uv run pytest -q tests/unit/test_public_release_docs.py` reported
    `76 passed`; `uv run pytest -q tests/unit/test_release_versions.py
    tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
    reported `106 passed`; strict OpenSpec validation for
    `require-beta-candidate-validation` was valid.
- Makefile architecture-check repair on 2026-06-14T02:03:58Z:
  `make architecture-check` now uses `PYTHON ?= .venv/bin/python` and passed
  with `proxy architecture checks passed`; release-doc tests pin that
  release-critical Makefile plumbing.
- `uv run pytest -q tests/unit/test_ci_workflow_required_checks.py`
  - Result: `4 passed`.
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_ci_workflow_required_checks.py`
  - Result: `74 passed` after adding the approval-preflight active OpenSpec,
    public release preflight script guards, live snapshot guard,
    PR/contributor post-approval proof-command guidance, and the active
    unchecked-task PR-head gate regression.
- `uv run pytest -q tests/unit/test_public_release_docs.py`
  - Result: `75 passed` after adding the local artifact proof helper coverage
    and preserving approval-gated release PR guidance for the live blocker
    snapshot, PR-head proof, publish-readiness, and post-publish proof commands.
- `./scripts/validate-active-openspec-changes.sh`
  - Result: `validated 54 active changes` after adding the
    `create-pytest-required-check-placeholders` PR-head evidence boundary.
- `uvx ruff format --check tests/unit/test_public_release_docs.py` and
  `uvx ruff check tests/unit/test_public_release_docs.py`
  - Result: `1 file already formatted`; `All checks passed!`.
- `npx --yes @fission-ai/openspec@latest validate add-anthropic-provider --strict`
  - Result: valid after refreshing the Claude session stickiness and
    quota-scoped cooldown task.
- `uv run pytest -q tests/integration/test_anthropic_proxy.py tests/unit/test_claude_lb_launch.py`
  - Result: `25 passed`.
- `uv run pytest -q tests/integration/test_proxy_sticky_sessions.py`
  - Result: `18 passed`.
- Combined focused release/OpenSpec parent-task proof:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_ci_workflow_required_checks.py tests/integration/test_anthropic_proxy.py tests/unit/test_claude_lb_launch.py tests/integration/test_proxy_sticky_sessions.py`
  - Result: `107 passed in 5.32s`.
- `npx --yes @fission-ai/openspec@latest validate --specs`
  - Result: all specs valid (`30 passed, 0 failed`) after the
    `decompose-proxy-service` spec-delta repair.
- `git diff --check`
  - Result: clean after the OpenSpec full-sweep and Makefile repairs.
- Remaining unchecked OpenSpec tasks after the cleanup are limited to GitHub
  CI/Codex-review confirmation tasks that require a committed PR head.
- `uv run python - <<'PY' ... yaml.safe_load(...)`
  - Result: `.github/workflows/publish-beta-release.yml: ok` and
    `.github/workflows/release.yml: ok` after adding the existing-prerelease
    artifact-dispatch path and tag-scoped release concurrency.

Screenshot artifacts refreshed on 2026-06-13:

- `docs/screenshots/accounts.jpg`
- `docs/screenshots/accounts-dark.jpg`
- `docs/screenshots/dashboard.jpg`
- `docs/screenshots/dashboard-dark.jpg`
- `docs/screenshots/login.jpg`
- `docs/screenshots/settings.jpg`
- `docs/screenshots/settings-dark.jpg`

Public screenshot artifact guard:

- `tests/unit/test_public_release_docs.py` verifies README references for all
  seven public screenshots and checks each image is a non-empty 2880x1800 JPEG.
- The same public-release docs guard now verifies that the screenshot harness
  uses its repo-owned `127.0.0.1:4174` preview URL, disables existing-server
  reuse, and avoids hard-coded `localhost:4173` navigation.

The Browser plugin in-app browser was unavailable in this session
(`agent.browsers.list()` returned `[]`; `iab` was not available), so visible
verification used the Playwright screenshot harness plus local image inspection.

OpenSpec validation path:

- The local `openspec` executable is not installed in this checkout.
- The npm-distributed CLI works without adding a repo dependency:
  `npx --yes @fission-ai/openspec@latest validate --specs`.
- Direct fallback attempts remain unavailable locally: `uv run openspec`
  cannot spawn `openspec`, `uvx openspec` cannot resolve a registry package,
  and bare `npx --yes openspec validate --specs` cannot determine an
  executable.

## Live GitHub Snapshot

Checked against `aneym/agent-lb` on 2026-06-14 at 2026-06-14T05:18:11Z:

- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` completed and
  continued through expected missing public artifacts after printing
  `snapshotAt=2026-06-14T05:18:11Z` and
  `snapshot complete at 2026-06-14T05:18:11Z`.
- `gh pr list --repo aneym/agent-lb --state open ...` -> `[]`
- `gh release list --repo aneym/agent-lb --limit 10 --json tagName,name,isPrerelease,isDraft,publishedAt,isLatest,isImmutable,createdAt`
  -> one visible release: prerelease `v1.20.0-beta.3`, created
  `2026-06-11T19:57:43Z`, published `2026-06-11T19:57:52Z`, not draft, not
  latest, and not immutable
- `gh repo view aneym/agent-lb --json description,homepageUrl,repositoryTopics,isArchived,isPrivate,visibility,url,defaultBranchRef`
  -> public repo, not archived, not private, default branch `main`, URL
  `https://github.com/aneym/agent-lb`
- Hosted repo description is still stale:
  `Codex/ChatGPT multiple account load balancer & proxy with usage tracking, dashboard, and OpenCode-compatible endpoints`
- Hosted repo homepage is empty.
- `repositoryTopics` returned `null`
- `gh release view v1.20.0-beta.3 --repo aneym/agent-lb --json tagName,name,isPrerelease,isDraft,publishedAt,url,assets,body`
  -> prerelease, not draft, published `2026-06-11T19:57:52Z`, no assets
- The existing prerelease body is still stale relative to current local
  evidence; it is still the older pricing/warmup beta body and still contains
  the obsolete SQLite migration caveat.
- Recent branch workflow runs returned `[]` via
  `gh run list --repo aneym/agent-lb --branch main --limit 10 ...`
- Public artifact checks found no published package/container artifact:
  PyPI JSON for `agent-lb` returned 404, `python3 -m pip index versions agent-lb`
  found no matching distribution, and GHCR manifest lookups for
  `ghcr.io/aneym/agent-lb:1.20.0-beta.3`, `ghcr.io/aneym/agent-lb:beta`, and
  `ghcr.io/aneym/charts/agent-lb:1.20.0-beta.3` returned denied/not visible
- Latest standalone refresh on 2026-06-14T08:50:46Z printed
  `snapshotAt=2026-06-14T08:50:46Z`,
  `snapshotOptionalFailures=5`,
  `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
  and
  `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
  summarizing the PyPI JSON, pip-index, GHCR image tag, GHCR beta alias, and
  GHCR Helm chart misses plus the stale repo metadata, stale release body, and
  missing exact wheel/sdist release assets while completing the read-only
  snapshot. The PyPI JSON probe now also requires the exact PyPI wheel/sdist
  filenames once the package is visible. The hardened release-state checks found
  no blocker for the selected tag, release title, public release URL, published
  timestamp, prerelease flag, or draft status, and the repo-state checks found
  no blocker for public visibility, private/archive state, or default branch.
- Latest full-preflight refresh on 2026-06-14T08:38:26Z printed
  `snapshotAt=2026-06-14T08:38:26Z`,
  `snapshotOptionalFailures=5`,
  `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
  and
  `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
  with the same public blockers still present; no GitHub mutations were made.

No GitHub mutations were made.

## Remaining Release Work

- Decide whether to update live GitHub repo description/homepage/topics to
  match the refreshed README/`pyproject.toml`; this is account-visible and
  needs user approval before mutation. The exact command is staged in
  `HANDOFF.md#approval-packet`.
- Before any publish/release mutation, do one approval-scoped final dirty-diff
  read if the tree changes again, and run the read-only commit/PR readiness
  preflight staged in `HANDOFF.md#approval-packet`. The latest local audit has
  already covered account totals, subscription visibility, Anthropic quota
  diagnostics, trusted-proxy auth, HTTP bridge compatibility, migration/provider
  defaults, runtime release links, and macOS/frontend status sync.
- If package/container availability is part of the release bar, publish or
  attach artifacts only after user approval and only after the release-ready
  changes are committed/pushed and the selected release tag points at that
  candidate commit. Local sdist/wheel artifacts are ready, but PyPI/GHCR/release
  assets are not publicly visible as of the 2026-06-14T08:50:46Z standalone snapshot
  (`snapshotOptionalFailures=5`;
  `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
  `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`).
  The live snapshot, PR-head proof, publish-readiness guard, and post-publish proof
  script are staged in `HANDOFF.md#approval-packet`; the publish-readiness guard
  fails closed on tag drift, a non-`main` or non-HEAD local checkout, a dirty
  working tree, or missing/non-green returned current-head `main` workflow
  evidence, and the proof now fails closed on wrong
  PyPI/pip-index versions, missing GHCR manifests, missing Helm
  chart package, stale public repository metadata, or a GitHub prerelease
  without the exact wheel/sdist assets and the replacement release body. PR-template
  and contributor gates now require approval-gated publication work to name
  those commands.
- Treat the running local launchd service as pre-candidate runtime evidence
  until it is restarted/reinstalled from the approved candidate. It is healthy
  at `http://127.0.0.1:2455/health` and reports `currentVersion`
  `1.20.0-beta.3`, but
  `http://127.0.0.1:2455/api/runtime/version` returned the old upstream
  `releaseUrl` `https://github.com/Soju06/agent-lb/releases/latest` with
  response `checkedAt` `2026-06-14T04:35:55.088884Z` during the earlier
  read-only check; a 2026-06-14T07:19:03Z runtime-proof refresh still failed the
  runtime-version assertion while health passed. No restart was performed
  because the service is healthy and restarts are approval-gated. After
  approval, rerun
  `./scripts/public-release-runtime-proof.sh v1.20.0-beta.3`; it is expected
  non-zero until the restarted/reinstalled daemon serves the fork release URL.
- Replace the stale `v1.20.0-beta.3` prerelease body after approval; a
  public-release-readiness draft without the obsolete migration caveat is
  staged in `HANDOFF.md#approval-packet`.

## Stop / Pause Rules

- Stop before any account-visible GitHub action unless the user explicitly asks
  for it in the current release-readiness context.
- Do not mark the public-release goal complete until the remaining release work
  above has either passed or has a user-accepted explicit blocker.
