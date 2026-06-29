# Handoff - agent-lb public release readiness

Date: 2026-06-13
Repo: `/Users/aneyman/repos/agent-lb`
Branch: `main`
Remote: `origin -> https://github.com/aneym/agent-lb.git`

## Goal

Finish getting `agent-lb` ready for public release end to end: README/package
metadata, OpenSpec-backed behavior changes, screenshots, agent onboarding
rules/skills, release gates, and live GitHub PR/release evidence.

## Current State

This handoff replaces stale `swap-lb` / `feat/anthropic-provider` notes. The
working tree is intentionally dirty with release-readiness work from several
surfaces. Preserve unrelated edits and continue from the current diff.

Public metadata has been refreshed so the repo describes the product as a
ChatGPT and Claude account load balancer. Package maintainer metadata now names
Alex Neyman for the public fork while preserving upstream authorship, and
package project URLs point at `https://github.com/aneym/agent-lb`.
`.all-contributorsrc` targets `aneym/agent-lb` so future contributor-table
automation does not drift back to `Soju06/codex-lb`. A trusted-proxy security
runbook that had been placed under `docs/` was moved into OpenSpec:

```text
openspec/changes/harden-trusted-proxy-api-key-auth/
```

Other active OpenSpec changes already present:

```text
openspec/changes/hide-canceled-subscription-accounts/
openspec/changes/fix-anthropic-quota-selection-diagnostics/
openspec/changes/fix-menubar-limit-status-sync/
openspec/changes/fix-runtime-release-repository/
openspec/changes/require-beta-candidate-validation/
```

The release-specific OpenSpec set is strict-valid. A follow-up hygiene pass
also cleared stale "CLI unavailable" validation tasks from older active changes
using the documented npm-distributed `@fission-ai/openspec` CLI fallback.
A full active OpenSpec sweep on 2026-06-14T02:03:58Z strict-validates all
54 active changes. The previously empty `decompose-proxy-service` change now
carries a normative `proxy-service-architecture` delta covering the stable
proxy facade, private `_service` domains, compatibility shims, and
architecture ratchets. Public release docs tests now fail if an active change
lacks spec-delta headers, and the Makefile architecture ratchet now uses the
repo Python contract (`PYTHON ?= .venv/bin/python`) instead of a missing bare
`python`. The approval preflight now runs
`./scripts/validate-active-openspec-changes.sh` so future release handoffs rerun
the full active-change strict sweep directly.

Release screenshots were regenerated through 2026-06-14T03:17:46Z:

```text
docs/screenshots/accounts.jpg
docs/screenshots/accounts-dark.jpg
docs/screenshots/dashboard.jpg
docs/screenshots/dashboard-dark.jpg
docs/screenshots/login.jpg
docs/screenshots/settings.jpg
docs/screenshots/settings-dark.jpg
```

Public release docs tests now pin those seven README screenshot references and
verify each referenced JPEG is present at the expected 2880x1800 Playwright
capture size. They also assert `docs/screenshots/` contains only those seven
README-backed assets, so older tracked but unreferenced screenshots cannot
re-enter the public bundle. They also pin the screenshot harness to the repo-owned
`127.0.0.1:4174` Playwright preview URL and ensure existing local preview
servers are not reused. They also pin the README GitHub metadata header against the
approval-packet repo description, homepage, topic commands, and resource
targets below.
Topic updates are staged through GitHub's replace-all topics API rather than
add-only flags, so stale public topics are removed when the approved command
runs. The same docs regression suite parses the staged replacement prerelease
notes and guards against reintroducing the obsolete SQLite migration caveat
from the current live prerelease body. The approval packet's post-publish proof
script and expanded commands now fail closed on wrong PyPI/pip-index versions,
missing GHCR manifests, missing Helm chart package, stale public repository
metadata, or a GitHub prerelease without the exact wheel/sdist assets or the
expected release title/public URL/published timestamp and replacement release
body.
The release workflow now also fails closed when `dist/` contains anything other
than the exact expected wheel and sdist filenames before the generic `dist/*`
artifact upload step runs.
The approval packet also includes a read-only publish-readiness guard that
fails closed when the selected release tag does not point at `HEAD`, when the
checkout is not local `main` at `HEAD`, or when the working tree is dirty, and
also fails closed after local eligibility if returned current-head `main`
workflow evidence is missing/non-green. This keeps
local-only release overlays and unproved cloud states from being published by
accident.
The read-only approval preflight now also verifies the dependency lock is
current, runs the live public blocker snapshot, and runs the release-version
regression suite before any approved commit, tag, PR, release, or publication
mutation.
The approval packet now includes a paste-ready PR draft with summary, test
plan, screenshot paths, approval-gated publication status, and OpenSpec
coverage. Public release docs tests pin that draft so it cannot silently drop
release proof, screenshot proof, active OpenSpec references, or mutating-command
boundaries. Contributor and PR-template release proof gates now require the
local artifact proof command, live blocker snapshot command, PR-head proof
command, runtime proof command, publish-readiness command, and post-publish
proof command for
approval-gated publication PRs.

Migration compatibility for metadata-created or partially bootstrapped
databases now belongs in `app/db/migrate.py`. The already-committed Alembic
revision files for subscription ledger columns and seed target offset minutes
are intentionally kept as clean historical forward migrations.

Public onboarding/service-control drift was also tightened: the macOS service
installer, `GETTING-STARTED.md`, the `get-started` skill, and the menubar
client now agree on launchd label `com.aneyman.agent-lb`. README JSONC config
blocks are covered by a parser regression test so public client snippets cannot
silently lose commas again. Public release docs tests also pin the agent
onboarding runbook/skill contract for the service label, local port, canonical
runbook delegation, and Claude subscription-billing guardrail.
The `get-started` skill activation rules now match public Anthropic/Claude,
OpenAI/ChatGPT, Claude Code, Codex, OpenCode, and OpenClaw setup prompts, with a
subprocess regression proving those prompts suggest the onboarding skill. The
skill closeout now points `/v1` clients at the same discovered-model
verification path as the canonical runbook.
The `agent-lb-account-operator` skill is now part of the public skill
activation surface too: account, billing, subscription-ledger, browser-profile,
pause/reactivate, remove, and verification prompts suggest the account operator
skill. That skill now clarifies OpenAI/ChatGPT vs Anthropic/Claude before
touching browser state or API rows when the provider is not named, and its
example registry includes both OpenAI and Anthropic dedicated Chrome profile
entries with null account identifiers and no-secrets notes.
`GETTING-STARTED.md` now hands ongoing quota reset checks, stuck or
rate-limited account triage, pause/reactivate routing, billing/subscription
changes, and browser-profile work to the `agent-lb-account-operator` skill plus
the local `.agent-lb/account-profiles.json` registry. Public skill activation
rules now also cover quota reset, stuck/disabled/rate-limited accounts,
subscription/account status, routing imbalance, and pause/reactivate routing
support prompts. `AGENTS.md` now exposes the same account-operations handoff
for agents entering through the repo instructions instead of the onboarding
runbook, and `README.md` now gives public readers the same post-setup
"account operator" cue for quota, stuck/rate-limited account,
billing/subscription, pause/reactivate routing, verification, and
browser-profile work.
The account-operator skill body now explicitly covers those account-specific
support paths too, including quota reset checks, stuck/rate-limited account
triage, subscription/account status checks, routing imbalance diagnostics, and
pause/reactivate routing requests.
The README's paste-ready "For AI Agents" prompt now repeats the high-risk
guardrails agents need before they even open the full runbook: one account at a
time, never set Claude API/auth token env vars when routing through the load
balancer, and show dotfile edits before applying them.

Public beta install docs now match current prerelease artifact semantics.
`README.md` presents source checkout as the always-available service/API path,
names CLI account auth for fresh clones, tells source users to build the
frontend before dashboard use, pins Docker examples to
`ghcr.io/aneym/agent-lb:1.20.0-beta.3`, pins uvx to `agent-lb==1.20.0b3`, and
no longer advertises the unpublished `latest` image tag. The top-level
Kubernetes example and Helm chart README OCI examples now include
`--version 1.20.0-beta.3 --devel` for install and upgrade commands, and chart
metadata describes the ChatGPT and Claude public fork. Public release docs
tests also pin generated release-workflow and beta-publish prerelease install
notes so prereleases keep pinned uvx/Docker/Helm `--devel` commands while
stable releases keep the latest-safe install commands. The runtime-portability
context for Codex session retagging also pins its Docker examples to
`ghcr.io/aneym/agent-lb:1.20.0-beta.3` instead of the unpublished `latest` tag.
The `Publish Beta Release` workflow now dispatches `release.yml` when the
matching GitHub prerelease already exists, so re-running beta publication cannot
stop at updated release notes while leaving PyPI, Docker, Helm, and release
assets unpublished. The `Release` workflow now scopes concurrency by the
selected release tag for both GitHub release events and manual dispatches.

Release-managed CODEOWNERS now include `@aneym` alongside upstream ownership
for release-please files, `CHANGELOG.md`, `app/__init__.py`, and `uv.lock`, so
the public fork owner is named on release-critical paths.

Agent git workflow policy now matches the public fork split: development on
`aneym/agent-lb` stays on `main`, while branch/PR conventions apply to upstream
`Soju06/codex-lb` contribution work. Beta guard, superseded-beta cleanup, and
Codex-label sync tests now model `aneym/agent-lb` as the release repo. Public
release docs tests now also pin the GitHub automation defaults directly:
all-contributors falls back to `aneym/agent-lb`, Codex review label sync keys
required checks by `aneym/agent-lb`, and neither script may drift back to
`Soju06/agent-lb`.

.github/CONTRIBUTING.md now documents the automated beta release-candidate
path: the synced `release/beta-*` PR, candidate-SHA validation checklist,
publish guard, and dirty-tree publishing warning.

The beta release guard now requires exactly one live upstream/account smoke
checklist choice. A release-candidate PR that checks both the live-smoke and
not-required items now fails before beta tag or prerelease publication, and the
generated checklist plus release-management OpenSpec document that rule.

Release-managed version checks now treat `uv.lock` prerelease spellings as the
PEP 440-normalized form of the same logical beta release. For `v1.20.0-beta.3`,
`pyproject.toml`, app, frontend, and Helm metadata use `1.20.0-beta.3`, while
the `uv.lock` package entry uses `1.20.0b3`. The release-management OpenSpec
and `require-beta-candidate-validation` change both record this rule.

GitHub issue/discussion intake forms and `SECURITY.md` now track the current
`1.20.0-beta.3` release train instead of stale `1.17.0`/`1.16.0` examples.
The bug/account/feature templates now use provider-neutral wording for ChatGPT
and Claude pools, and the bug form spells `OpenCode` with ASCII characters.
The bug form names the advertised public clients directly: Codex, Claude Code,
OpenCode, OpenClaw, OpenAI-compatible SDKs, and Anthropic-compatible SDKs. The
feature request form also includes the Anthropic-compatible API surface and
client launchers/integrations as scoped areas. Public bug, account/quota,
feature-request, and Q&A templates now route security vulnerability reports to
GitHub private advisories instead of public intake threads. The PR template now uses
provider-faithful wording so OpenAI/Codex and Anthropic/Claude wire-format
paths are both called out for contributors, and it requires screenshots or a
clear not-applicable reason for dashboard/UI-visible changes. The PR template
and contributor gates now also require public client/onboarding changes to keep
`AGENTS.md`, `README.md`, `GETTING-STARTED.md`, the `get-started` skill,
public-release skill activation rules, and docs regression coverage in sync, or
explain unaffected surfaces.
They also require public client, release-version, account-plan, and
support-intake changes to keep the bug report, account quota, feature-request,
and Q&A intake forms plus public-release regression coverage in sync.
They also require security/support-window changes to keep `SECURITY.md`, README,
Helm README, and public-release regression coverage in sync when supported
versions, release train, artifact names, vulnerability reporting, or
published-artifact security wording changes.
They also require approval-gated release/package/publication PRs to name the
local artifact proof command, live blocker snapshot command, PR-head proof
command, runtime proof command, publish-readiness command, and post-publish
proof command.
They also require account admin, dedicated browser-profile, billing,
subscription-ledger, pause/reactivate, removal, and verification changes to
keep `AGENTS.md`, README, `GETTING-STARTED.md`, the
`agent-lb-account-operator` skill, its example account profile registry, public
skill activation rules, and public-release regression coverage in sync.
`SECURITY.md` now describes Docker, Helm, and PyPI as published artifacts once
available, matching the approval-gated beta artifact state instead of implying
the current beta image/chart/package is already visible.

Runtime release-link drift was tightened too: `/api/runtime/version` now checks
and links to the public `aneym/agent-lb` release repository, backed by
`openspec/changes/fix-runtime-release-repository/`.
Read-only live daemon check: the launchd service on `http://127.0.0.1:2455` is
healthy and reports `currentVersion` `1.20.0-beta.3`, but
`http://127.0.0.1:2455/api/runtime/version` still returned stale
`releaseUrl` `https://github.com/Soju06/agent-lb/releases/latest` with
response `checkedAt` `2026-06-14T04:35:55.088884Z` during a read-only check at
`2026-06-14T04:36:45Z`; source/tests still assert the candidate
`https://github.com/aneym/agent-lb/releases/latest` URL. No restart was
performed because the service is healthy and service restarts are
approval-gated. The read-only runtime proof
`./scripts/public-release-runtime-proof.sh v1.20.0-beta.3` now fails closed
with expected non-zero `rc=1` before an approved restart/reinstall: health
passed, version metadata parsed, and the runtime assertion returned `false` at
`2026-06-14T07:19:03Z` after printing
`runtimeProofAt=2026-06-14T07:19:03Z`; the healthy daemon still serves the
stale upstream release URL.

Main OpenSpec `Purpose` placeholders from archived changes were replaced with
concise capability purposes. `openspec/specs/**` no longer contains the
archive-generated placeholder purpose text. The opsx sync command and matching
skill now tell agents to write concise capability purposes instead of leaving
placeholder text in new main specs.

A targeted release audit also found and fixed account-summary subscription
status drift for existing database rows. Account ledger status values are now
normalized before API serialization, so mixed-case or padded stored values such
as ` CANCELED ` return the canonical API status.

The final local dirty-diff release audit covered the remaining high-risk slices:
subscription visibility, trusted-proxy auth, runtime release links, Anthropic
quota diagnostics, HTTP bridge compatibility, migration/provider defaults, and
macOS/frontend status sync. No additional local code blockers were found.

`scripts/public-release-drift-scan.sh` now turns the final public-surface drift
audit into a reusable preflight gate for stale project names, old release
examples, deleted screenshot artifacts, unpublished Docker `latest` install
shortcuts, and stale hosted-release descriptions across README, onboarding,
Helm docs, GitHub templates/workflows, skills, main OpenSpec specs, and package
metadata.

The active OpenSpec task ledger now records read-only live PR/run evidence for
both remaining unchecked PR-head CI/Codex-review gates. Public-release docs
tests now fail if any active unchecked task is not one of those PR-head gates or
does not record the live PR and CI-run evidence boundary.

## Important Rules

- Work on `main`; do not create or switch branches unless the user asks.
- Do not push, publish, create releases, update GitHub metadata, or open PRs
  without fresh explicit user approval.
- Behavior/API/schema/dashboard/proxy changes need OpenSpec coverage.
- Do not edit `CHANGELOG.md` directly.
- Do not put feature or behavior docs under `docs/`; use OpenSpec context docs.
- Use `GETTING-STARTED.md` and the `get-started` skill as the onboarding source
  of truth.

## Verification Already Run

Broad backend gate:

```bash
uv run pytest -q
```

Result:

- `3675 passed, 43 skipped, 4 warnings in 213.60s`

Lint, formatting, and proxy architecture:

```bash
uvx ruff format --check .
uvx ruff check .
PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make lint
git diff --check
```

Results:

- `661 files already formatted`
- `All checks passed!`
- `proxy architecture checks passed`
- `git diff --check` clean

Frontend:

```bash
cd frontend
bun run test
bun run screenshots
bun run lint
bun run build
```

Results:

- Vitest: `89 passed (89)` test files, `589 passed (589)` tests
- Screenshots: `7 passed`
- Lint: passed
- Build: passed

macOS menubar:

```bash
cd clients/macos-menubar
swift test
```

Result:

- `111 tests, 0 failures`

Focused backend reruns that covered the previous full-suite failure clusters:

```bash
uv run pytest -q tests/integration/test_http_responses_bridge.py
uv run pytest -q tests/integration/test_proxy_sticky_sessions.py tests/integration/test_repositories.py tests/integration/test_migrations.py tests/unit/test_db_migrate.py tests/unit/test_images_schemas.py tests/unit/test_model_refresh_scheduler.py tests/integration/test_settings_api.py
uv run pytest -q tests/unit/test_trusted_proxy_client_ip.py tests/integration/test_trusted_proxy_auth.py
uv run pytest -q tests/unit/test_accounts_service_transitions.py tests/unit/test_proxy_load_balancer_refresh.py tests/integration/test_anthropic_proxy.py tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py
uv run pytest -q tests/unit/test_claude_lb_launch.py
uv run pytest -q tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py tests/integration/test_public_usage.py tests/unit/test_proxy_load_balancer_refresh.py tests/integration/test_migrations.py tests/unit/test_db_migrate.py
uv run pytest -q tests/unit/test_public_release_docs.py
uv run pytest -q tests/unit/test_runtime_version.py tests/integration/test_runtime_api.py
uv run pytest -q tests/integration/test_accounts_api.py tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py tests/unit/test_accounts_service_transitions.py
uv run pytest -q tests/unit/test_trusted_proxy_client_ip.py tests/integration/test_trusted_proxy_auth.py tests/unit/test_runtime_version.py tests/integration/test_runtime_api.py
uv run pytest -q tests/integration/test_anthropic_proxy.py tests/integration/test_http_responses_bridge.py tests/integration/test_proxy_sticky_sessions.py tests/integration/test_migrations.py tests/unit/test_db_migrate.py tests/integration/test_settings_api.py tests/unit/test_claude_lb_launch.py tests/unit/test_proxy_load_balancer_refresh.py tests/integration/test_accounts_api.py tests/integration/test_dashboard_overview.py tests/integration/test_reports_api.py tests/integration/test_public_usage.py tests/unit/test_accounts_service_transitions.py
```

Results:

- `77 passed in 10.15s`
- `209 passed, 3 skipped, 2 warnings in 17.51s`
- `15 passed in 0.66s`
- `102 passed, 3 skipped in 4.97s`
- `10 passed in 0.02s`
- `131 passed, 6 skipped, 2 warnings in 15.85s`
- `4 passed in 0.03s`
- `11 passed in 0.27s`
- `48 passed in 4.92s`
- `26 passed in 0.87s`
- `290 passed, 6 skipped, 2 warnings in 34.54s`; covers Anthropic diagnostics,
  HTTP bridge compatibility, migrations/provider defaults, account subscription
  visibility/checks, dashboard/report/public usage, settings, and launcher
  formatting

Installer/docs regression:

```bash
bash scripts/install-service.sh --print | rg 'com\.aneyman\.agent-lb|com\.agent-lb'
npx --yes @fission-ai/openspec@latest validate macos-menubar-app --strict
```

Results:

- Generated plist contains `com.aneyman.agent-lb`.
- `Change 'macos-menubar-app' is valid`.

Public beta install-doc and GitHub intake regression:

```bash
uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py
rg -n "ghcr\.io/aneym/agent-lb:latest|uvx agent-lb" README.md deploy/helm/agent-lb/README.md
```

Results:

- Earlier broad rerun: `53 passed`; covers README/Helm prerelease artifact
  pins, Helm OCI upgrade pins, chart metadata, package metadata, release
  workflow and beta-publish prerelease install notes, CODEOWNERS, README JSONC
  snippets, agent onboarding runbook/skill invariants, GitHub issue/discussion
  version placeholders, security-policy supported-version train,
  security-policy published-artifact wording,
  provider-neutral ChatGPT/Claude intake options with explicit
  OpenAI/Anthropic API-key-only no-account labels, ASCII `OpenCode` spelling,
  source-checkout API/unbuilt-dashboard guidance, release workflow
  tag-scoped concurrency, explicit Claude Code/Anthropic Python SDK/OpenClaw/SDK client choices,
  Anthropic/client-integration
  feature request scopes, README Client Setup provider-surface guidance, and
  Kubernetes version policy, plus the seven public README screenshot references
  and 2880x1800 JPEG artifact checks, plus README/HANDOFF GitHub metadata
  description/homepage/topic/resource drift checks, replacement prerelease notes
  without the obsolete SQLite migration caveat, and PR-template dual-provider
  protocol guidance, plus CONTRIBUTING beta release-candidate flow, plus
  `get-started`
  skill activation for public Anthropic/Claude, OpenAI/ChatGPT, Claude Code,
  Codex, OpenCode, and OpenClaw setup prompts, plus unique public-client rows,
  canonical onboarding endpoint coverage for the README Client Setup matrix,
  the Anthropic Python SDK bearer-auth root-base example,
  the existing-prerelease artifact-dispatch branch in `Publish Beta Release`,
  and OpenClaw coverage in public repo topics plus package and Helm chart
  keywords, plus a discovered-model `/v1` smoke path for OpenCode, OpenClaw,
  and SDK users, including the `get-started` skill's final-verification
  contract. The focused public-release docs suite now also covers fail-closed
  post-publish proof script plus expanded command contract for PyPI, pip-index,
  GHCR image tags, the Helm chart package, GitHub repository metadata, GitHub
  release assets with exact wheel/sdist filenames, the replacement release body, the package
  `Development Status :: 4 - Beta` classifier, and a timeless UTC quota-reset
  placeholder in the public account/quota intake form, plus the read-only
  commit/PR readiness preflight in this handoff and the PR template's
  screenshot-proof requirement for dashboard/UI-visible changes, plus the
  preflight's locked-dependency and release-version regression checks, plus the
  PR/contributor public client/onboarding sync gate for README,
  `GETTING-STARTED.md`, the `get-started` skill, and public docs regression
  coverage, plus PR/contributor release-proof guidance that names
  `./scripts/public-release-postpublish-proof.sh <approved-release-tag>`, plus
  README/Helm guidance that OCI chart commands are
  approval-gated until the beta chart artifact is published and source-chart
  install commands remain available before publication, plus account-operator
  skill activation, dual-provider profile examples, provider clarification,
  no-secrets, billing-action confirmation guardrails, and PR/contributor
  account-operator sync gates.
- Account-operator wiring rerun:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `53 passed`; `uv run python -m json.tool
  .agents/skills/skill-rules.json` -> passed; `uv run python -m json.tool
  .agents/skills/agent-lb-account-operator/account-profiles.example.json` ->
  passed.
- Skill-rules sync-gate rerun after adding `.agents/skills/skill-rules.json`
  to the PR/contributor public-onboarding and account-operator sync rules:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `53 passed in 0.52s`; `uvx ruff format --check
  tests/unit/test_public_release_docs.py` -> `1 file already formatted`;
  `uvx ruff check tests/unit/test_public_release_docs.py` -> `All checks
  passed!`; `uv run python -m json.tool .agents/skills/skill-rules.json` ->
  passed; `git diff --check` -> clean.
- Agent-rules sync-gate rerun after adding `AGENTS.md` to the PR/contributor
  public-onboarding sync rules:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `53 passed in 0.43s`; `uvx ruff format --check
  tests/unit/test_public_release_docs.py` -> `1 file already formatted`;
  `uvx ruff check tests/unit/test_public_release_docs.py` -> `All checks
  passed!`; `uv run python -m json.tool .agents/skills/skill-rules.json` ->
  passed; `git diff --check` -> clean.
- Public support-intake sync-gate rerun after requiring bug report, account
  quota, feature-request, and Q&A templates to stay aligned with public clients,
  release-version examples, and account-plan choices:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `54 passed in 0.43s`; `uvx ruff format --check
  tests/unit/test_public_release_docs.py` -> `1 file already formatted`;
  `uvx ruff check tests/unit/test_public_release_docs.py` -> `All checks
  passed!`; `uv run python -m json.tool .agents/skills/skill-rules.json` ->
  passed; `git diff --check` -> clean.
- Security/support policy sync-gate rerun after requiring security policy,
  README, Helm README, and public-release regression coverage to stay aligned
  with supported-version, release-train, package/container artifact,
  vulnerability-reporting, and published-artifact security wording changes:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `55 passed in 0.39s`; `uvx ruff format --check
  tests/unit/test_public_release_docs.py` -> `1 file already formatted`;
  `uvx ruff check tests/unit/test_public_release_docs.py` -> `All checks
  passed!`; `git diff --check` -> clean.
- Private-advisory intake rerun after routing public bug, account/quota,
  feature-request, and Q&A security-vulnerability reports to GitHub private
  advisories:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `56 passed in 0.47s`; `uvx ruff format --check
  tests/unit/test_public_release_docs.py` -> `1 file already formatted`;
  `uvx ruff check tests/unit/test_public_release_docs.py` -> `All checks
  passed!`; `git diff --check` -> clean.
- Account-operator public support prompt rerun after routing ongoing
  account/quota/status and routing-imbalance prompts from `README.md`,
  `AGENTS.md`, `GETTING-STARTED.md`, and `.agents/skills/skill-rules.json` to
  the account-operator skill:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `59 passed`; `uv run python -m json.tool
  .agents/skills/skill-rules.json` -> passed. The same suite now pins the
  PR/contributor account-operator sync gates so these public surfaces stay
  aligned with the skill, example registry, activation rules, and regression
  tests.
- Final blocker-snapshot alignment rerun after updating
  `tests/unit/test_public_release_docs.py` to expect `2026-06-14T01:51:15Z`
  and adding the paste-ready PR draft guard:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `60 passed`; `uvx ruff format --check
  tests/unit/test_public_release_docs.py` -> `1 file already formatted`;
  `uvx ruff check tests/unit/test_public_release_docs.py` -> `All checks
  passed!`; `git diff --check` -> clean.
- Paste-ready PR draft cleanup on 2026-06-14T05:46:47Z:
  the staged handoff PR draft now replaces the `Fill this in` related-issue
  placeholder with a no-tracked-issue statement. `uv run pytest -q
  tests/unit/test_public_release_docs.py` -> `76 passed`; Ruff format/check for
  `tests/unit/test_public_release_docs.py` and `git diff --check` passed.
- Strict OpenSpec rerun after refreshing the pending PR-head CI/Codex-review
  task evidence to `2026-06-14T01:51:15Z`:
  `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict`
  -> `Change 'require-beta-candidate-validation' is valid`.
- Account-operator PR-template scope rerun after expanding the checklist to
  pin pause/reactivate, removal, and verification guidance alongside
  browser-profile, billing, and subscription-ledger changes:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `59 passed`.
- Broader release-facing preflight rerun:
  `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_cleanup_superseded_beta_prs.py tests/unit/test_sync_codex_ok_labels.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `109 passed in 4.45s`; `uv run python -m scripts.verify_release_version --tag
  v1.20.0-beta.3 --require-channel beta` -> `channel=beta`,
  `pypi_version=1.20.0b3`; `uv lock --locked` -> current lockfile accepted;
  `npx --yes @fission-ai/openspec@latest validate --specs` ->
  `30 passed, 0 failed`; `curl -fsS http://127.0.0.1:2455/health` ->
  `{"status":"ok"}` without restarting the local service.
- Frontend screenshot/package artifact refresh:
  `cd frontend && bun run test` -> `89 passed (89)` test files and
  `589 passed (589)` tests; `cd frontend && bun run screenshots` ->
  `7 passed` on the latest deterministic rerun after the harness was hardened
  to use its own `127.0.0.1:4174` preview URL; all seven public README
  screenshots are `2880x1800`, with `dashboard.jpg` and `accounts.jpg`
  visually inspected as nonblank and correctly framed; `file
  docs/screenshots/*.jpg` plus README/reference scans on
  2026-06-14T04:18:50Z confirmed exactly those seven `2880x1800` JPEGs remain
  and no public docs reference the deleted `apis-assigned-accounts` or
  `codex-session-retag-*` screenshot artifacts; `cd frontend && bun run lint` and
  `cd frontend && bun run build` passed; `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make package`
  passed frontend build, import smoke, wheel/sdist build, and wheel asset
  verification; `uvx --from twine==6.2.0 twine check dist/*` passed; the sdist
  forbidden-path scan returned no matches; `uv sync --dev --frozen` restored
  the dev environment after packaging.
- Stale blocker-snapshot marker scan across `GOAL.md`, `HANDOFF.md`, and
  `tests/unit/test_public_release_docs.py` -> no matches.
- The stale `latest`/bare `uvx agent-lb` scan returned no matches.
- Every public Helm OCI install/upgrade command in README/Helm docs is paired
  with `--version 1.20.0-beta.3` and `--devel`.
- Focused README agent-prompt rerun:
  `uv run pytest -q tests/unit/test_public_release_docs.py` -> `19 passed in
  0.07s`; covers the canonical runbook pointer, one-account-at-a-time OAuth
  flow, Claude subscription-billing guardrail, and dotfile-approval instruction.
- Focused GitHub intake rerun after adding Claude Code/OpenClaw/SDK client
  options and Anthropic/client-integration feature scopes:
  `uv run pytest -q tests/unit/test_public_release_docs.py` -> `19 passed in
  0.07s`.
- Stale-public-string scan after the final intake alignment:
  `rg -n "1\.16\.0|1\.17\.0|Codex/ChatGPT|ОpenCode|com\.agent-lb|ghcr\.io/aneym/agent-lb:latest" .github README.md GETTING-STARTED.md AGENTS.md deploy/helm/agent-lb/README.md pyproject.toml`
  -> no matches.

Latest hygiene rerun:

```bash
uvx ruff format --check .
uvx ruff check .
git diff --check
```

Results:

- `661 files already formatted`
- `All checks passed!`
- `git diff --check` clean
- Latest focused public-doc hygiene after GitHub intake/security tests:
  `1 file already formatted`; `All checks passed!`; `git diff --check` clean.
- Latest focused public-doc hygiene after client-intake template alignment:
  `1 file already formatted`; `All checks passed!`; `git diff --check` clean.
- Latest focused public-doc hygiene after adding the screenshot artifact guard:
  `1 file already formatted`; `All checks passed!`; `git diff --check` clean.
- Latest focused public-doc hygiene after adding the beta-publish workflow
  guard: `1 file already formatted`; `All checks passed!`; `git diff --check`
  clean.
- Latest focused public-doc hygiene after tightening PR-template screenshot
  proof: `1 file already formatted`; `All checks passed!`; `git diff --check`
  clean.

Release/version guardrail rerun:

```bash
uv lock --locked
uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py
uvx ruff format --check scripts/release_versions.py scripts/guard_beta_release.py tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py
uvx ruff check scripts/release_versions.py scripts/guard_beta_release.py tests/unit/test_release_versions.py tests/unit/test_guard_beta_release.py tests/unit/test_public_release_docs.py
uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3 --require-channel beta
git diff --check
```

Results:

- `uv lock --locked`: accepted the current lockfile (`Resolved 120 packages in 4ms`);
  `uv.lock` keeps only the intended `agent-lb` version normalization diff, and
  the final approval preflight now runs this before any approved mutation.
- Approval-preflight release/doc pytest:
  `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `108 passed`.
- Staged replacement prerelease notes refresh:
  paste-ready notes now name the read-only public-release preflight and drift
  scan plus the local artifact proof and runtime proof helpers, and
  public-release docs tests pin the current `79 passed`, `109 passed`, and
  `08:38` full-preflight evidence instead of older intermediate counts.
  2026-06-14T07:10:20Z refresh: the staged notes body now rejects the stale
  `04:51:13Z`, `77 passed`, `107 passed`, and `79 passed` focused-evidence
  bullets while keeping the latest live snapshot and local artifact proof.
  After the 2026-06-14T08:50:46Z standalone live snapshot, the paste-ready PR draft
  regression now pins the shared latest live snapshot timestamp and the
  hardened release-title/public-URL/published-timestamp release-state evidence
  instead of accepting the older `06:36:14Z` snapshot as current.
  After the 2026-06-14T08:38:25Z full preflight, the staged notes now carry the
  fresh preflight, live snapshot, local artifact proof, and `79`/`109` focused
  test-count evidence.
- Approval-preflight beta guard:
  `uv run pytest -q tests/unit/test_guard_beta_release.py`
  -> `10 passed in 2.38s`.
- Approval-preflight release verifier:
  `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3 --require-channel beta`
  -> `channel=beta`, `pypi_version=1.20.0b3`.
- Approval-preflight one-command script:
  `./scripts/public-release-preflight.sh v1.20.0-beta.3`
  -> passed on 2026-06-14T04:51:13Z, including release helper `bash -n`
  syntax checks with the drift-scan, PR-head proof, and local artifact proof
  helpers, plus the read-only public release drift scan.
  It passed again on 2026-06-14T05:31:47Z after the preflight script began
  printing `preflightAt=2026-06-14T05:31:47Z` and the publish-readiness guard
  began verifying tag/channel mapping before the dirty-tree block.
  It passed again on the current tree on 2026-06-14T05:43:20Z after printing
  `preflightAt=2026-06-14T05:43:20Z`; this rerun included read-only PR/run
  checks (`[]`/`[]`), locked dependency verification, helper syntax checks,
  drift scan, local artifact proof, release-version/public-doc/Kubernetes
  tests, beta guard, release-version verifier, `validated 54 active changes`,
  main specs `30 passed, 0 failed`, Ruff, and whitespace.
  It passed again after the PR draft cleanup on 2026-06-14T05:47:09Z after
  printing `preflightAt=2026-06-14T05:47:09Z`; this rerun included read-only
  PR/run checks (`[]`/`[]`), locked dependency verification, helper syntax
  checks, drift scan, local artifact proof
  (`localArtifactProofAt=2026-06-14T05:47:10Z`), `106 passed`, beta guard,
  release-version verifier, `validated 54 active changes`, main specs
  `30 passed, 0 failed`, Ruff, and whitespace.
  It passed again after the live blocker snapshot was wired directly into the
  preflight on 2026-06-14T05:51:19Z after printing
  `preflightAt=2026-06-14T05:51:19Z`; this rerun included read-only PR/run
  checks (`[]`/`[]`), the live public snapshot
  (`snapshotAt=2026-06-14T05:51:19Z`), local artifact proof
  (`localArtifactProofAt=2026-06-14T05:51:22Z`), `106 passed`, beta guard,
  release-version verifier, `validated 54 active changes`, main specs
  `30 passed, 0 failed`, Ruff, and whitespace.
- Approval-packet local artifact proof alignment on 2026-06-14T05:00:08Z:
  `HANDOFF.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and
  `.github/CONTRIBUTING.md` now name
  `./scripts/public-release-local-artifact-proof.sh <approved-release-tag>`
  alongside the live snapshot, PR-head, publish-readiness, and post-publish
  proof commands. `uv run pytest -q tests/unit/test_public_release_docs.py`
  -> `76 passed`; `uv run pytest -q tests/unit/test_release_versions.py
  tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `106 passed`; `./scripts/public-release-preflight.sh v1.20.0-beta.3`
  passed end to end after the approval-packet update.
- Publish-readiness guard:
  `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3`
  -> expected non-zero on 2026-06-14T05:35:44Z after printing
  `publishReadinessAt=2026-06-14T05:35:44Z`; the tag matched `HEAD`, then the
  guard verified `channel=beta` and `pypi_version=1.20.0b3`, printed
  `dirty_count=167`, listed the current dirty/untracked paths, and exited
  non-zero before any publication command.
- Approval-preflight active OpenSpec changes:
  `./scripts/validate-active-openspec-changes.sh`
  -> `validated 54 active changes`.
- Approval-preflight OpenSpec specs:
  `npx --yes @fission-ai/openspec@latest validate --specs`
  -> `30 passed, 0 failed`.
- Strict OpenSpec release-change refresh on 2026-06-14T01:33:06Z:
  `hide-canceled-subscription-accounts`, `fix-runtime-release-repository`,
  `harden-trusted-proxy-api-key-auth`,
  `fix-anthropic-quota-selection-diagnostics`,
  `fix-menubar-limit-status-sync`, and `require-beta-candidate-validation`
  all validated with `--strict`.
- Approval-preflight public-doc hygiene:
  `uvx ruff format --check tests/unit/test_public_release_docs.py && uvx ruff check tests/unit/test_public_release_docs.py && git diff --check`
  -> `1 file already formatted`; `All checks passed!`; `git diff --check`
  clean.
- Earlier combined release-version/beta-guard/public-doc pytest:
  `88 passed in 4.18s`.
- Beta publish/public-doc pytest after adding the existing-prerelease dispatch
  guard, tag-scoped release concurrency, Anthropic Python SDK README example,
  Vercel AI SDK README example, and
  release/package publication artifact-evidence gate, and live public snapshot
  artifact blockers: `86 passed in 3.48s`.
- Fork-policy/automation/public-doc pytest after aligning repo-owner fixtures:
  `99 passed in 3.75s`.
- Public-doc/Kubernetes pytest after pinning runtime-portability Docker retag
  examples to the prerelease image, guarding staged replacement release notes,
  fail-closed post-publish artifact proof script, README metadata resources,
  timeless account/quota reset placeholder, the commit/PR readiness preflight,
  SDK/app wiring guard, Anthropic Python SDK README example, Vercel AI SDK README example, PR template screenshot
  proof, release/package publication artifact-evidence gate, and live public
  snapshot artifact blockers, plus PR/contributor public client/onboarding sync,
  plus PR/contributor account-operator sync, plus beta Helm OCI/source-install
  gating, plus public issue-chooser routing and markdown code-fence balance:
  `76 passed`.
- GitHub automation/defaults pytest after pinning all-contributors and Codex
  label-sync scripts to the public fork: `47 passed in 0.20s`.
- GitHub metadata/release-notes approval-packet pytest after switching topic
  updates to the replace-all GitHub topics API and pinning the staged
  replacement prerelease body plus fail-closed post-publish artifact proof
  commands: `39 passed in 0.21s`.
- Public doc/OpenSpec/workflow scan for
  `ghcr.io/aneym/agent-lb:latest`: no matches outside the negative regression
  tests.
- GitHub automation stale-owner scan: no `Soju06/agent-lb` references in the
  all-contributors or Codex label-sync scripts; the only matches are the
  negative public-release regression assertions.
- Fork-policy stale-reference scan: no matches for `Soju06/agent-lb` or the
  obsolete line that treated `aneym/agent-lb` as upstream in the patched
  release automation and agent convention files.
- `bash -n scripts/public-release-drift-scan.sh scripts/public-release-preflight.sh && ./scripts/public-release-drift-scan.sh`
  on 2026-06-14T04:24:37Z
  - Result: passed; no unpublished Docker `latest` install shortcuts, stale
    upstream runtime release URL, deleted public screenshot artifact references,
    retired fork names, or stale hosted-repo description text remain in the
    scanned public release surfaces.
- Public-doc/Kubernetes pytest after recording the final public-surface scan
  evidence, refreshing the live blocker snapshot, and adding the paste-ready PR
  draft guard, issue-chooser, markdown fence, and screenshot-harness guards:
  `76 passed`.
- Focused public-release docs Ruff after recording the final public-surface scan
  evidence and refreshing the live blocker snapshot: `1 file already formatted`;
  `All checks passed!`.
- Strict OpenSpec validation after refreshing the pending PR-head CI/Codex-review
  task evidence: `Change 'require-beta-candidate-validation' is valid`.
- Focused ruff format/check after the fork-policy alignment: `4 files already
  formatted`; `All checks passed!`.
- Ruff format: `5 files already formatted`.
- Ruff check: `All checks passed!`.
- Release verifier: `channel=beta`, `pypi_version=1.20.0b3`.
- `git diff --check` clean.
- Workflow YAML parse after the dispatch/concurrency changes:
  `.github/workflows/publish-beta-release.yml: ok` and
  `.github/workflows/release.yml: ok`.
- Live-smoke exclusivity pytest after tightening beta PR checklist parsing:
  `10 passed`.
- Public-doc/Kubernetes pytest after pinning the exact-one live-smoke
  CONTRIBUTING rule, staged prerelease notes, the commit/PR readiness preflight,
  and PR template screenshot proof: `45 passed`.
- Focused ruff after the live-smoke exclusivity guard:
  `3 files already formatted`; `All checks passed!`.
- OpenSpec strict validation for `require-beta-candidate-validation`: valid.
- `git diff --check` clean.

Visible screenshot verification note:

- The Browser plugin in-app browser was unavailable
  (`agent.browsers.list()` returned `[]`; `iab` was not available).
- Verification used the Playwright screenshot harness and local image
  inspection instead.

OpenSpec validation:

```bash
npx --yes @fission-ai/openspec@latest validate harden-trusted-proxy-api-key-auth --strict
npx --yes @fission-ai/openspec@latest validate hide-canceled-subscription-accounts --strict
npx --yes @fission-ai/openspec@latest validate fix-anthropic-quota-selection-diagnostics --strict
npx --yes @fission-ai/openspec@latest validate fix-menubar-limit-status-sync --strict
npx --yes @fission-ai/openspec@latest validate fix-runtime-release-repository --strict
npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict
npx --yes @fission-ai/openspec@latest validate --specs
```

Results:

- The 6 release-specific active changes listed above are valid.
- All specs are valid: `30 passed, 0 failed`.
- The latest focused strict validation rerun covered
  `harden-trusted-proxy-api-key-auth`, `fix-runtime-release-repository`, and
  `require-beta-candidate-validation`; all three changes are valid.
- A follow-up stale-validation cleanup also strict-validated
  `add-claude-fable-pricing`, `fix-token-invalidated-account-state`,
  `add-reports-page`, `surface-anthropic-session-route-errors`,
  `create-pytest-required-check-placeholders`, and
  `rate-limit-aware-retry-and-resume`; it validated
  `add-auth-guardian-refresh` and `add-fill-first-routing-strategy` through
  `validate --specs`, and recorded the command evidence in those task ledgers.
- A second active-change validation cleanup on 2026-06-14T01:55:40Z
  strict-validated `add-account-subscription-ledger`,
  `fix-public-usage-window-backfill`, and
  `restore-codex-image-generation-tool`; active task and verify reports now
  avoid stale OpenSpec CLI-unavailable deferrals, with regression coverage in
  `tests/unit/test_public_release_docs.py`.
- Active OpenSpec full-sweep repair on 2026-06-14T02:03:58Z:
  `decompose-proxy-service` now carries a `proxy-service-architecture` spec
  delta; `npx --yes @fission-ai/openspec@latest validate
  decompose-proxy-service --strict` passed; a strict loop over all active
  change folders reported `validated 54 active changes`.
- Approval-preflight active OpenSpec script on 2026-06-14T02:13:06Z:
  `./scripts/validate-active-openspec-changes.sh` -> `validated 54 active
  changes`.
- Makefile architecture-check repair:
  `make architecture-check` now invokes `.venv/bin/python` through
  `PYTHON ?= .venv/bin/python` and passed with
  `proxy architecture checks passed`.
- The workflow placeholder regression check for
  `create-pytest-required-check-placeholders` passed:
  `uv run pytest -q tests/unit/test_ci_workflow_required_checks.py` -> `4 passed`.
- Read-only PR-head gate refresh on 2026-06-14T07:20:58Z:
  `gh pr list --repo aneym/agent-lb --state open` -> `[]`;
  `gh run list --repo aneym/agent-lb --branch main --limit 10` -> `[]`;
  `git status --porcelain | wc -l` -> `167`.
- PR-head proof helper dry-run on 2026-06-14T05:40:38Z:
  `./scripts/public-release-pr-head-proof.sh 0` -> expected `exit_status=1`
  after printing `prHeadProofAt=2026-06-14T05:40:38Z`; `gh pr view` reported
  `no pull requests found`, so real PR-head proof remains pending until a
  release PR exists.
- PR-head proof helper SHA-identity hardening on 2026-06-14T08:17:46Z:
  `./scripts/public-release-pr-head-proof.sh 0` -> expected `exit_status=1`
  after printing `prHeadProofAt=2026-06-14T08:17:46Z`; `gh pr view` still
  reported `no pull requests found`. Successful PR-head proofs now print
  `pr_head_sha` and `pr_head_short`, then require the current-head Codex
  classifier output to include the same `head=${pr_head_short}` fragment before
  treating CI/Codex-review evidence as matched to the proved commit.
- PR-head proof timestamp regression sweep on 2026-06-14T05:42:14Z:
  `uv run pytest -q tests/unit/test_public_release_docs.py` -> `76 passed`;
  `uv run pytest -q tests/unit/test_release_versions.py
  tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
  -> `106 passed`; strict validation for `require-beta-candidate-validation`
  and `create-pytest-required-check-placeholders` -> valid;
  `openspec validate --specs` -> `30 passed, 0 failed`; Ruff format/check for
  `tests/unit/test_public_release_docs.py` and `git diff --check` passed.
- PR-head proof SHA-identity verification on 2026-06-14T08:18:57Z:
  shell syntax passed; `uv run pytest -q tests/unit/test_public_release_docs.py`
  -> `78 passed`; release-version/public-doc/K8s slice -> `108 passed`;
  `require-beta-candidate-validation` strict validation -> valid; main specs ->
  `30 passed, 0 failed`; Ruff and whitespace -> clean.
  Dirty tree count remains `167`.
- Active unchecked-task regression after documenting that PR-head boundary and
  PR-head proof helper:
  `uv run pytest -q tests/unit/test_public_release_docs.py` -> `73 passed`;
  `npx --yes @fission-ai/openspec@latest validate create-pytest-required-check-placeholders --strict`
  -> valid.
- PR template and contributor release-proof regression after requiring the
  PR-head proof command in approval-gated publication PR guidance on
  2026-06-14T03:42:05Z:
  `uv run pytest -q tests/unit/test_public_release_docs.py` -> `73 passed`;
  `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict`
  -> valid; `uvx ruff format --check tests/unit/test_public_release_docs.py`
  -> `1 file already formatted`; `uvx ruff check
  tests/unit/test_public_release_docs.py` -> `All checks passed!`;
  `git diff --check` -> passed.
- Focused release-doc/workflow regression after recording the OpenSpec cleanup:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_ci_workflow_required_checks.py`
  -> `74 passed`.
- Active OpenSpec sweep after recording the PR-head boundary:
  `./scripts/validate-active-openspec-changes.sh` -> `validated 54 active
  changes`.
- Focused public-release docs Ruff after the active spec-delta and Makefile
  guards: `uvx ruff format --check tests/unit/test_public_release_docs.py`
  -> `1 file already formatted`; `uvx ruff check
  tests/unit/test_public_release_docs.py` -> `All checks passed!`.
- The old `add-anthropic-provider` parent task for Claude session stickiness and
  quota-scoped cooldown routing is now closed with fresh evidence:
  `npx --yes @fission-ai/openspec@latest validate add-anthropic-provider --strict`
  -> valid; `uv run pytest -q tests/integration/test_anthropic_proxy.py tests/unit/test_claude_lb_launch.py`
  -> `25 passed`; `uv run pytest -q tests/integration/test_proxy_sticky_sessions.py`
  -> `18 passed`.
- Combined focused proof after updating the release handoff:
  `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_ci_workflow_required_checks.py tests/integration/test_anthropic_proxy.py tests/unit/test_claude_lb_launch.py tests/integration/test_proxy_sticky_sessions.py`
  -> `107 passed in 5.32s`.
- All-spec validation after the active full-sweep repair:
  `npx --yes @fission-ai/openspec@latest validate --specs` ->
  `30 passed, 0 failed`; `git diff --check` -> clean.
- Remaining unchecked OpenSpec tasks are now limited to GitHub CI/Codex-review
  confirmation tasks that require a committed PR head.
- The latest all-spec validation was rerun after replacing archived `Purpose`
  placeholders and again after documenting normalized `uv.lock` prerelease
  spelling; all specs are valid (`30 passed, 0 failed`).
- A placeholder scan across `.agents/commands/opsx/sync.md`,
  `.agents/skills/openspec-sync-specs/SKILL.md`, and `openspec/specs` returned
  no matches after tightening the opsx sync command and matching skill wording.
- The local `openspec` executable is not installed, but the npm-distributed
  `@fission-ai/openspec` CLI works without adding a repo dependency.
- Direct fallback attempts remain unavailable locally: `uv run openspec`
  cannot spawn `openspec`, `uvx openspec` cannot resolve a registry package,
  and bare `npx --yes openspec validate --specs` cannot determine an
  executable.

Local package artifacts:

```bash
PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make package
ls -lh dist
tar -tzf dist/agent_lb-1.20.0b3.tar.gz | rg '^agent_lb-1\.20\.0b3/(\.agents|\.github|clients|frontend|tests|docs|openspec|\.build|__pycache__|\.venv|node_modules)(/|$)' || true
uvx --from twine==6.2.0 twine check dist/*
uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3
unzip -p dist/agent_lb-1.20.0b3-py3-none-any.whl agent_lb-1.20.0b3.dist-info/METADATA | rg -n '^(Name|Version|Summary|Author|Maintainer|Maintainer-email|Keywords|Classifier):'
```

Results:

- Reran after public README/service-label/runtime-release, package metadata
  summary alignment, and OpenClaw topic/keyword edits; frontend production
  build passed, import smoke passed, Hatch built the wheel from the sdist, and
  `scripts/verify-wheel-assets.py` passed.
- `dist/agent_lb-1.20.0b3-py3-none-any.whl` is `1.3M`.
- `dist/agent_lb-1.20.0b3.tar.gz` is `1.1M`.
- The sdist has no top-level `.agents`, `.github`, `clients`, `frontend`,
  `tests`, `docs`, `openspec`, `.build`, `__pycache__`, `.venv`, or
  `node_modules` matches.
- `twine check` passed for both the wheel and sdist.
- Version/tag mapping passed with `pypi_version=1.20.0b3`.
- Wheel metadata includes `Maintainer: Alex Neyman`, `Summary: ChatGPT and
  Claude account load balancer & proxy with usage tracking, dashboard, and
  OpenAI/Anthropic-compatible endpoints`,
  OpenAI/ChatGPT/Claude/Anthropic/OpenCode/OpenClaw keywords, and
  `Classifier: Development Status :: 4 - Beta`.

Focused client reruns after the final audit:

```bash
cd frontend
bun run test src/features/accounts/components/account-detail.test.tsx src/features/accounts/components/accounts-page.test.tsx src/features/accounts/hooks/use-accounts.test.ts src/features/accounts/schemas.test.ts src/test/mocks/handler-coverage.test.ts src/__integration__/accounts-flow.test.tsx

cd ../clients/macos-menubar
swift test --filter 'AccountFilterTests|ModelDecodingTests|ServiceControllerTests'
```

Results:

- Frontend account suite: `6 passed (6)` test files, `21 passed (21)` tests.
- Menubar suite: `43 tests, 0 failures`.

## Live GitHub Snapshot

Checked against `aneym/agent-lb` on 2026-06-14 at 2026-06-14T05:18:11Z:

```bash
./scripts/public-release-live-snapshot.sh v1.20.0-beta.3
```

Results:

- Helper stamp: `snapshotAt=2026-06-14T05:18:11Z`; final line
  `snapshot complete at 2026-06-14T05:18:11Z`
- Open PRs: `[]`
- Visible releases: one prerelease `v1.20.0-beta.3`, created
  `2026-06-11T19:57:43Z`, published `2026-06-11T19:57:52Z`, not draft, not
  latest, and not immutable
- Repo visibility: public; not archived; not private
- Default branch: `main`
- Repo URL: `https://github.com/aneym/agent-lb`
- Release `v1.20.0-beta.3` is a prerelease, not a draft, published
  `2026-06-11T19:57:52Z`, with no assets.
- Release body is stale relative to the current local evidence: it is still the
  older pricing/warmup beta body and still says
  `tests/integration/test_migrations.py` fails on a SQLite duplicate-column
  path, but the current local focused migration suite passed.
- Hosted repo description is stale relative to local docs:
  `Codex/ChatGPT multiple account load balancer & proxy with usage tracking, dashboard, and OpenCode-compatible endpoints`
- Hosted repo homepage is empty.
- `repositoryTopics` returned `null`
- Recent branch workflow runs returned `[]`
- Public artifact checks found no published package/container artifact:
  PyPI JSON for `agent-lb` returned 404, `python3 -m pip index versions
  agent-lb` found no matching distribution, and GHCR manifest lookups for
  `ghcr.io/aneym/agent-lb:1.20.0-beta.3` and `ghcr.io/aneym/agent-lb:beta`
  returned denied/not visible; the Helm chart manifest
  `ghcr.io/aneym/charts/agent-lb:1.20.0-beta.3` also returned denied/not
  visible.
- The live snapshot helper completed and continued through the expected
  pre-publication PyPI, pip-index, GHCR image, and Helm chart misses.
- Refresh: the one-command approval preflight reran this helper at
  `snapshotAt=2026-06-14T05:51:19Z`; open PRs and recent branch workflow runs
  still returned `[]`, the prerelease still has no assets, PyPI remains 404,
  and the GHCR image/chart manifests remain denied/not visible.
- Refresh: the helper reran at `snapshotAt=2026-06-14T05:55:14Z`; open
  PRs and recent branch workflow runs still returned `[]`, the prerelease still
  has no assets and the older pricing/warmup body, hosted repo metadata is still
  stale/empty, PyPI remains 404, and the GHCR image/chart manifests remain
  denied/not visible.
- Refresh: the helper reran at `snapshotAt=2026-06-14T05:59:27Z` and
  printed `snapshotOptionalFailures=5`; open PRs and recent branch workflow
  runs still returned `[]`, the prerelease still has no assets and the older
  pricing/warmup body, hosted repo metadata is still stale/empty, PyPI remains
  404, and the GHCR image/chart manifests remain denied/not visible.
- Refresh: the helper reran inside the full preflight at
  `snapshotAt=2026-06-14T06:03:30Z` and printed
  `snapshotOptionalFailures=5`; open PRs and recent branch workflow runs still
  returned `[]`, the prerelease still has no assets and the older
  pricing/warmup body, hosted repo metadata is still stale/empty, PyPI remains
  404, and the GHCR image/chart manifests remain denied/not visible.
- Refresh: the helper reran at `snapshotAt=2026-06-14T06:09:45Z` and
  printed `snapshotOptionalFailures=5` plus
  `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
  open PRs and recent branch workflow runs still returned `[]`, the prerelease
  still has no assets and the older pricing/warmup body, hosted repo metadata
  is still stale/empty, PyPI remains 404, and the same named public artifact
  probes remain missing.
- Refresh: the helper reran inside the full preflight at
  `snapshotAt=2026-06-14T06:11:16Z` and printed
  `snapshotOptionalFailures=5` plus
  `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
  open PRs and recent branch workflow runs still returned `[]`, the prerelease
  still has no assets and the older pricing/warmup body, hosted repo metadata
  is still stale/empty, PyPI remains 404, and the same named public artifact
  probes remain missing.
- Latest refresh: the helper reran inside the full preflight at
  `snapshotAt=2026-06-14T07:03:24Z` and printed
  `snapshotOptionalFailures=5`,
  `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
  and
  `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
  open PRs and recent branch workflow runs still returned `[]`, the prerelease
  still has no assets and the older pricing/warmup body, hosted repo metadata
  is still stale/empty, PyPI remains 404, and the same named public artifact
  probes remain missing.

No GitHub mutations were made.

## Approval Packet

These are the exact account-visible actions to run only after the user gives
fresh approval in the current thread.

Release-tag precondition:

- `v1.20.0-beta.3^{}` currently peels to
  `b00efd4fce34f42edb455a78b9cf34df8600e337`, matching current `HEAD`, but the
  working tree still has 167 dirty/untracked paths. A workflow dispatch for
  `v1.20.0-beta.3` would publish committed tag contents only. Do not run an
  artifact publish as the release-ready build until the approved changes are
  committed/pushed and the chosen release tag points at that candidate commit.
  Prefer a new beta tag over moving the already-published prerelease tag unless
  the maintainer explicitly approves retagging.

Commit / PR readiness preflight:

Run this before asking for or performing any commit, push, tag, PR, release, or
publication mutation. These checks are intentionally local/read-only; do not add
`git commit`, `git push`, `gh pr create`, `gh repo edit`, `gh release edit`,
`gh workflow run`, or `gh api` commands to this preflight.
The preferred single-command form is
`./scripts/public-release-preflight.sh <approved-release-tag>`; the expanded
command list below is the script contract.

```bash
git status --short
git diff --stat
git rev-parse HEAD
git rev-parse '<approved-release-tag>^{}'
gh pr list --repo aneym/agent-lb --state open --json number,title,headRefName,baseRefName,state,url,updatedAt
gh run list --repo aneym/agent-lb --branch main --limit 10 --json databaseId,status,conclusion,workflowName,headBranch,headSha,createdAt,url
uv lock --locked
bash -n scripts/public-release-preflight.sh scripts/public-release-drift-scan.sh scripts/public-release-live-snapshot.sh scripts/public-release-publish-readiness.sh scripts/public-release-postpublish-proof.sh scripts/public-release-pr-head-proof.sh scripts/public-release-local-artifact-proof.sh scripts/public-release-runtime-proof.sh scripts/validate-active-openspec-changes.sh
./scripts/public-release-drift-scan.sh
./scripts/public-release-live-snapshot.sh <approved-release-tag>
./scripts/public-release-local-artifact-proof.sh <approved-release-tag>
uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py
uv run pytest -q tests/unit/test_guard_beta_release.py
uv run python -m scripts.verify_release_version --tag <approved-release-tag> --require-channel beta
./scripts/validate-active-openspec-changes.sh
npx --yes @fission-ai/openspec@latest validate --specs
uvx ruff format --check tests/unit/test_public_release_docs.py
uvx ruff check tests/unit/test_public_release_docs.py
git diff --check
```

After a release PR exists, prove the current PR head with:

```bash
./scripts/public-release-pr-head-proof.sh <pr-number>
```

Runtime proof after approved restart/reinstall:

```bash
./scripts/public-release-runtime-proof.sh <approved-release-tag>
```

This check is read-only and is expected non-zero until the restarted or
reinstalled local daemon serves the approved fork release URL.

Live blocker snapshot:

Use this read-only command to refresh current PR, workflow, release, repository
metadata, PyPI, pip-index, GHCR image, GHCR alias, and Helm chart visibility
before asking for approval:

```bash
./scripts/public-release-live-snapshot.sh <approved-release-tag>
```

Publish readiness guard:

Run this after the release-ready tree is committed/tagged and before any
publication command. It is intentionally local/read-only and fails closed if the
release tag does not point at `HEAD`, if the working tree is dirty, or if
returned current-head `main` workflow evidence is missing/non-green.

Preferred command:

```bash
./scripts/public-release-publish-readiness.sh <approved-release-tag>
```

Current expected result:

- `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3` is expected
  non-zero because the tag currently matches `HEAD` and local `main`, but the
  working tree is dirty with 167 dirty/untracked paths. The latest read-only
  refresh printed `publishReadinessAt=2026-06-14T08:34:12Z`, confirmed
  `current_branch=main`, confirmed tag, `HEAD`, and `main_sha` are all
  `b00efd4fce34f42edb455a78b9cf34df8600e337`, verified `channel=beta` and
  `pypi_version=1.20.0b3`, then printed `dirty_count=167` before listing those
  paths and exiting before any live PR/run probe. On a clean release-ready tree,
  the guard must reach the open-PR and main-run probes, print `open_pr_count`
  and `current_head_main_run_count`, fail closed if the current `HEAD` has no
  returned `main` workflow run evidence or a returned current-head run is not
  completed with a success, skipped, or neutral conclusion, then print
  `publish readiness passed at ${PUBLISH_READINESS_AT}` before any artifact
  publish is treated as release-ready.

Pull request draft after approval:

```markdown
Title:
chore(release): prepare public beta release readiness

Related issue / discussion:
No tracked issue identified in the local readiness pass; add one before opening
only if the maintainer wants the release PR attached to a specific issue.

## Summary

- Refresh public README/package/Helm metadata, onboarding runbooks, agent skills,
  support intake forms, PR/contributor gates, and release notes around the
  `aneym/agent-lb` public fork and ChatGPT + Claude account pooling.
- Harden release-risk paths for subscription-usable account counts,
  subscription status normalization, trusted-proxy API-key bypasses, runtime
  release links, Anthropic quota diagnostics, HTTP bridge compatibility,
  provider/default migration compatibility, and macOS menubar status sync.
- Regenerate public dashboard, accounts, settings, login, and dark-mode
  screenshots, and pin those README screenshot references with regression
  coverage.
- Tighten beta release-candidate automation so stale, forked, contradictory, or
  insufficiently validated beta candidates cannot publish, while existing
  prerelease reruns still dispatch PyPI, Docker, Helm, and release-asset
  publication.

## Test plan

- `uv run pytest -q` -> `3675 passed, 43 skipped, 4 warnings in 213.60s`
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make lint` -> proxy architecture checks and Ruff passed
- `cd frontend && bun run test && bun run screenshots && bun run lint && bun run build` -> Vitest, screenshots, lint, and build passed
- `cd clients/macos-menubar && swift test` -> `111 tests, 0 failures`
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make package` -> wheel/sdist built and wheel assets verified
- `uvx --from twine==6.2.0 twine check dist/*` -> passed
- `./scripts/public-release-local-artifact-proof.sh v1.20.0-beta.3` -> passed on 2026-06-14T05:35:44Z after printing `localArtifactProofAt=2026-06-14T05:35:44Z`; verified the selected tag's local wheel/sdist, sdist README freshness, dev-only path exclusion, wheel metadata, README image tag references, and `twine check`
- `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3 --require-channel beta` -> `channel=beta`, `pypi_version=1.20.0b3`
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T04:51:13Z, including release helper `bash -n` syntax checks with the drift-scan, PR-head proof, and local artifact proof helpers plus the read-only public release drift scan
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T05:31:47Z after printing `preflightAt=2026-06-14T05:31:47Z` and after the publish-readiness tag/channel-before-dirty guard update
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T05:43:20Z after printing `preflightAt=2026-06-14T05:43:20Z`; included local artifact proof, `106 passed`, beta guard, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T05:47:09Z after printing `preflightAt=2026-06-14T05:47:09Z`; included local artifact proof, `106 passed`, beta guard, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T05:51:19Z after printing `preflightAt=2026-06-14T05:51:19Z`; included live public snapshot `snapshotAt=2026-06-14T05:51:19Z`, local artifact proof, `106 passed`, beta guard, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:03:29Z after printing `preflightAt=2026-06-14T06:03:29Z`; included live public snapshot `snapshotAt=2026-06-14T06:03:30Z` with `snapshotOptionalFailures=5`, local artifact proof `localArtifactProofAt=2026-06-14T06:03:33Z`, `106 passed`, beta guard, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:11:15Z after printing `preflightAt=2026-06-14T06:11:15Z`; included live public snapshot `snapshotAt=2026-06-14T06:11:16Z` with `snapshotOptionalFailures=5` and `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T06:11:19Z`, `106 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:16:55Z after printing `preflightAt=2026-06-14T06:16:55Z`; included live public snapshot `snapshotAt=2026-06-14T06:16:56Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T06:16:58Z`, `106 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:25:32Z after printing `preflightAt=2026-06-14T06:25:32Z`; included live public snapshot `snapshotAt=2026-06-14T06:25:33Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T06:25:36Z`, `106 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:36:13Z after printing `preflightAt=2026-06-14T06:36:13Z`; included live public snapshot `snapshotAt=2026-06-14T06:36:14Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T06:36:17Z`, `107 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:44:35Z after printing `preflightAt=2026-06-14T06:44:35Z`; included live public snapshot `snapshotAt=2026-06-14T06:44:36Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T06:44:39Z`, `107 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:49:17Z after printing `preflightAt=2026-06-14T06:49:17Z`; included live public snapshot `snapshotAt=2026-06-14T06:49:17Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T06:49:20Z`, `107 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T06:55:29Z after printing `preflightAt=2026-06-14T06:55:29Z`; included live public snapshot `snapshotAt=2026-06-14T06:55:29Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T06:55:32Z`, `107 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace; the release-asset blocker now requires the exact `agent_lb-1.20.0b3-py3-none-any.whl` and `agent_lb-1.20.0b3.tar.gz` GitHub release assets
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T07:03:23Z after printing `preflightAt=2026-06-14T07:03:23Z`; included live public snapshot `snapshotAt=2026-06-14T07:03:24Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T07:03:27Z`, `108 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace; the release workflow now verifies exact wheel/sdist dist filenames before the generic `dist/*` upload
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T07:15:03Z after printing `preflightAt=2026-06-14T07:15:03Z`; included live public snapshot `snapshotAt=2026-06-14T07:15:04Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T07:15:07Z`, `108 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T07:22:54Z after printing `preflightAt=2026-06-14T07:22:54Z`; included read-only PR/run checks `[]`/`[]`, live public snapshot `snapshotAt=2026-06-14T07:22:55Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T07:22:57Z`, `108 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T07:37:07Z after printing `preflightAt=2026-06-14T07:37:07Z`; included read-only PR/run checks `[]`/`[]`, live public snapshot `snapshotAt=2026-06-14T07:37:08Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T07:37:10Z`, `108 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T07:48:03Z after printing `preflightAt=2026-06-14T07:48:03Z`; included read-only PR/run checks `[]`/`[]`, live public snapshot `snapshotAt=2026-06-14T07:48:04Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T07:48:07Z`, `108 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T07:56:47Z after printing `preflightAt=2026-06-14T07:56:47Z`; included read-only PR/run checks `[]`/`[]`, live public snapshot `snapshotAt=2026-06-14T07:56:48Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T07:56:51Z`, `108 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace; the PyPI JSON probe now requires exact `agent_lb-1.20.0b3-py3-none-any.whl` and `agent_lb-1.20.0b3.tar.gz` filenames once PyPI is visible
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T08:07:12Z after printing `preflightAt=2026-06-14T08:07:12Z`; included read-only PR/run checks `[]`/`[]`, live public snapshot `snapshotAt=2026-06-14T08:07:12Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T08:07:15Z`, `108 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight again on 2026-06-14T08:38:25Z after printing `preflightAt=2026-06-14T08:38:25Z`; included read-only PR/run checks `[]`/`[]`, live public snapshot `snapshotAt=2026-06-14T08:38:26Z` with `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, local artifact proof `localArtifactProofAt=2026-06-14T08:38:29Z`, `109 passed`, beta guard `10 passed`, release verifier, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `uv run pytest -q tests/unit/test_public_release_docs.py` -> `78 passed` after adding the release workflow exact dist artifact name gate before `Upload dist artifacts`
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `108 passed` after adding the exact dist artifact gate
- `uv run pytest -q tests/unit/test_guard_beta_release.py` -> `10 passed`
- `bash -n scripts/public-release-drift-scan.sh scripts/public-release-preflight.sh && ./scripts/public-release-drift-scan.sh` -> passed read-only public release drift scan on 2026-06-14T04:24:37Z
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot on 2026-06-14T05:18:11Z after printing `snapshotAt=2026-06-14T05:18:11Z`; public artifacts remain unpublished/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot on 2026-06-14T06:03:30Z after printing `snapshotAt=2026-06-14T06:03:30Z` and `snapshotOptionalFailures=5`; public artifacts remain unpublished/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot on 2026-06-14T06:09:45Z after printing `snapshotAt=2026-06-14T06:09:45Z`, `snapshotOptionalFailures=5`, and `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; public artifacts remain unpublished/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:11:16Z after printing `snapshotAt=2026-06-14T06:11:16Z`, `snapshotOptionalFailures=5`, and `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; public artifacts remain unpublished/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:16:56Z after printing `snapshotAt=2026-06-14T06:16:56Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; public artifacts, public repo metadata, release body, and release assets remain unpublished/stale/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:36:14Z after printing `snapshotAt=2026-06-14T06:36:14Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; public artifacts, public repo metadata, release body, and release assets remain unpublished/stale/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:44:36Z after printing `snapshotAt=2026-06-14T06:44:36Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, and draft status passed, while public artifacts, public repo metadata, release body, and release assets remain unpublished/stale/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:49:17Z after printing `snapshotAt=2026-06-14T06:49:17Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed, while public artifacts, public repo metadata, release body, and release assets remain unpublished/stale/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:55:29Z after printing `snapshotAt=2026-06-14T06:55:29Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed, while public artifacts, public repo metadata, release body, and exact wheel/sdist release assets remain unpublished/stale/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T07:03:24Z after printing `snapshotAt=2026-06-14T07:03:24Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed, while public artifacts, public repo metadata, release body, and exact wheel/sdist release assets remain unpublished/stale/not visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot on 2026-06-14T07:11:40Z after printing `snapshotAt=2026-06-14T07:11:40Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed, while open PRs/runs stayed empty, public artifacts, public repo metadata, release body, and exact wheel/sdist release assets remain unpublished/stale/not visible; no GitHub mutations were made
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot on 2026-06-14T08:34:06Z after printing `snapshotAt=2026-06-14T08:34:06Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; open PRs/runs stayed empty, public artifacts, public repo metadata, release body, and exact wheel/sdist release assets remain unpublished/stale/not visible; no GitHub mutations were made
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the full preflight on 2026-06-14T08:38:26Z after printing `snapshotAt=2026-06-14T08:38:26Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; open PRs/runs stayed empty, public artifacts, public repo metadata, release body, and exact wheel/sdist release assets remain unpublished/stale/not visible; no GitHub mutations were made
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed standalone read-only live blocker snapshot on 2026-06-14T08:50:46Z after printing `snapshotAt=2026-06-14T08:50:46Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; open PRs/runs stayed empty, the existing prerelease still has no assets and the older pricing/warmup body, hosted repo metadata is still stale/empty, PyPI remains 404, pip index still has no matching distribution, and GHCR image/chart manifests remain denied/not visible; no GitHub mutations were made
- 2026-06-14T08:32:28Z snapshot-evidence alignment -> paste-ready PR draft, goal brief, and release-publication blockers pinned the 08:27 standalone snapshot while preserving the 08:07 full-preflight evidence as history; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `79 passed`; release-version/public-doc/K8s slice -> `109 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- 2026-06-14T08:36:27Z live evidence refresh -> paste-ready PR draft, goal brief, release-publication blockers, and publish-readiness guard evidence now pin the 08:34 live snapshot/readiness results; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `79 passed`; release-version/public-doc/K8s slice -> `109 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- 2026-06-14T08:42:42Z full-preflight evidence refresh -> paste-ready PR draft, goal brief, release-publication blockers, and OpenSpec task ledger now pin the 08:38 preflight/snapshot/artifact-proof results; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `79 passed`; release-version/public-doc/K8s slice -> `109 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- 2026-06-14T08:45:50Z continuation guardrail -> public-release docs tests now pin the `Known Remaining Risk` section so the handoff keeps saying release completion still depends on commit/PR, public metadata, package/container assets, runtime restart, and publication approval gates; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `80 passed`; no GitHub mutations were made
- 2026-06-14T08:48:06Z OpenSpec completion-boundary hardening -> active release-management now requires the handoff to keep remaining-risk and completion status explicit until commit/PR, PR-head CI/Codex review, public metadata, package/container/chart artifacts, release assets/body, runtime restart/reinstall, and publication proof are complete or accepted; public-release docs tests pin that contract; 2026-06-14T08:48:54Z verification: `uv run pytest -q tests/unit/test_public_release_docs.py` -> `80 passed`; release-version/public-doc/K8s slice -> `110 passed`; `require-beta-candidate-validation` strict validation -> valid; Ruff and whitespace -> clean; no GitHub mutations were made
- 2026-06-14T08:50:46Z read-only live/readiness refresh -> live snapshot still reports the same public blockers and publish readiness still fails closed on `dirty_count=167`; open PRs/runs stayed empty, no public package/container/chart/release assets are visible, and no GitHub mutations were made
- 2026-06-14T08:54:03Z verification after refreshing the 08:50 live/readiness evidence -> `uv run pytest -q tests/unit/test_public_release_docs.py` -> `80 passed`; release-version/public-doc/K8s slice -> `110 passed`; `require-beta-candidate-validation` strict validation -> valid; Ruff and whitespace -> clean; no GitHub mutations were made
- `./scripts/public-release-runtime-proof.sh v1.20.0-beta.3` -> expected
  non-zero before approved restart/reinstall on 2026-06-14T07:19:03Z after
  printing `runtimeProofAt=2026-06-14T07:19:03Z` (`rc=1`; health passed,
  release metadata parsed, runtime assertion returned `false` because the
  healthy daemon still serves the old upstream release URL)
- `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3` -> expected non-zero before publication on 2026-06-14T06:21:30Z after printing `publishReadinessAt=2026-06-14T06:21:30Z`; the approved tag still points at `HEAD`, release metadata parsed, and the working tree is dirty (`167` dirty/untracked paths)
- `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3` -> expected non-zero before publication on 2026-06-14T07:13:20Z after printing `publishReadinessAt=2026-06-14T07:13:20Z`; the approved tag still points at `HEAD` (`b00efd4fce34f42edb455a78b9cf34df8600e337`), release metadata parsed with `channel=beta` and `pypi_version=1.20.0b3`, and the working tree is dirty (`dirty_count=167`)
- `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3` -> expected non-zero before publication on 2026-06-14T07:29:58Z after printing `publishReadinessAt=2026-06-14T07:29:58Z`; the approved tag still points at `HEAD` (`b00efd4fce34f42edb455a78b9cf34df8600e337`), release metadata parsed with `channel=beta` and `pypi_version=1.20.0b3`, and the working tree is dirty (`dirty_count=167`) before any live PR/run probe
- `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3` -> expected non-zero before publication on 2026-06-14T08:34:12Z after printing `publishReadinessAt=2026-06-14T08:34:12Z`; the approved tag still points at `HEAD`, `current_branch=main`, `main_sha=b00efd4fce34f42edb455a78b9cf34df8600e337`, release metadata parsed with `channel=beta` and `pypi_version=1.20.0b3`, and the working tree is dirty (`dirty_count=167`) before any live PR/run probe
- `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3` -> expected non-zero before publication on 2026-06-14T08:50:46Z after printing `publishReadinessAt=2026-06-14T08:50:46Z`; the approved tag still points at `HEAD`, `current_branch=main`, `main_sha=b00efd4fce34f42edb455a78b9cf34df8600e337`, release metadata parsed with `channel=beta` and `pypi_version=1.20.0b3`, and the working tree is dirty (`dirty_count=167`) before any live PR/run probe
- 2026-06-14T08:16:23Z local-main publish-readiness hardening verification -> shell syntax passed; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `78 passed`; release-version/public-doc/K8s slice -> `108 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3` -> expected non-zero before publication on 2026-06-14T04:36:34Z (`rc=1`; PyPI JSON returned 404); script now also proves public repository metadata and replacement release body freshness after publication
- `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3` -> expected non-zero before publication on 2026-06-14T05:35:44Z after printing `postpublishProofAt=2026-06-14T05:35:44Z` (`rc=1`; PyPI JSON returned 404 before the later GHCR/GitHub checks)
- 2026-06-14T07:34:03Z post-publish proof identity hardening refresh -> shell syntax passed; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `78 passed`; release-version/public-doc/K8s slice -> `108 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- `uv run pytest -q tests/unit/test_public_release_docs.py` -> `76 passed` after the runtime proof refresh
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `106 passed` after the runtime proof refresh
- `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict` -> valid after the post-publish proof refresh
- `uv run pytest -q tests/unit/test_public_release_docs.py` -> `77 passed`
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `106 passed`
- `uv run pytest -q tests/unit/test_guard_beta_release.py` -> `10 passed in 2.38s`
- `uv run pytest -q tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `79 passed`
- `npx --yes @fission-ai/openspec@latest validate --specs` -> `30 passed, 0 failed`
- `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict` -> `Change 'require-beta-candidate-validation' is valid`
- `./scripts/validate-active-openspec-changes.sh` -> `validated 54 active changes`
- `uv run pytest -q tests/unit/test_public_release_docs.py` -> `78 passed` after staged prerelease notes evidence refresh
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `108 passed` after staged prerelease notes evidence refresh
- `uv run pytest -q tests/unit/test_guard_beta_release.py` -> `10 passed` after staged prerelease notes evidence refresh
- `npx --yes @fission-ai/openspec@latest validate require-beta-candidate-validation --strict` -> `Change 'require-beta-candidate-validation' is valid` after staged prerelease notes evidence refresh
- `npx --yes @fission-ai/openspec@latest validate --specs` -> `30 passed, 0 failed` after staged prerelease notes evidence refresh
- `uv run ruff format --check tests/unit/test_public_release_docs.py` -> `1 file already formatted` after staged prerelease notes evidence refresh
- `uv run ruff check tests/unit/test_public_release_docs.py` -> `All checks passed!` after staged prerelease notes evidence refresh
- `git diff --check` -> passed after staged prerelease notes evidence refresh
- 2026-06-14T07:12:55Z focused verification after the live-snapshot evidence split: `uv run pytest -q tests/unit/test_public_release_docs.py` -> `78 passed`; release-version/public-doc/K8s slice -> `108 passed`; beta guard -> `10 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- `uvx ruff format --check tests/unit/test_public_release_docs.py && uvx ruff check tests/unit/test_public_release_docs.py && git diff --check` -> formatted, lint clean, whitespace clean

## Screenshots / output

- `docs/screenshots/dashboard.jpg`
- `docs/screenshots/dashboard-dark.jpg`
- `docs/screenshots/accounts.jpg`
- `docs/screenshots/accounts-dark.jpg`
- `docs/screenshots/settings.jpg`
- `docs/screenshots/settings-dark.jpg`
- `docs/screenshots/login.jpg`

`cd frontend && bun run screenshots` -> `7 passed` on 2026-06-14T03:17:46Z
against the repo-owned `127.0.0.1:4174` preview URL; each README screenshot is
covered by `tests/unit/test_public_release_docs.py` and verified as a non-empty
2880x1800 JPEG.

## Release / publication status

Publication is approval-gated and not run from this PR draft. As of the
`2026-06-14T08:38:26Z` full-preflight live snapshot, PyPI `agent-lb` still returns 404,
`python3 -m pip index versions agent-lb` finds no distribution, GHCR image/chart
manifests for `1.20.0-beta.3` are denied/not visible, and the GitHub prerelease
has no assets. The live snapshot summarized these as `snapshotOptionalFailures=5` and
`snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
with total public blockers captured as
`snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`.
The hardened release-state and repo-state checks found no blocker for the
selected tag, release title, public URL, published timestamp, prerelease flag,
draft status, public visibility, private/archive state, or default branch.
Refresh this state with
`./scripts/public-release-live-snapshot.sh <approved-release-tag>` before
asking for approval. Use the local artifact proof to verify the selected
wheel/sdist still match the candidate. After approval, first use the
publish-readiness guard to prove the approved tag points at a clean
release-ready tree. Once this PR exists, run the PR-head proof helper to prove
current-head CI/Codex review gates. After an approved local restart/reinstall,
run the runtime proof to close the daemon release-link blocker. Then use the
post-publish proof script in this handoff to prove PyPI version plus exact
wheel/sdist filenames, pip-index, Docker, Helm, and exact GitHub wheel/sdist
release assets. The preferred commands are
`./scripts/public-release-local-artifact-proof.sh <approved-release-tag>`,
`./scripts/public-release-live-snapshot.sh <approved-release-tag>`,
`./scripts/public-release-pr-head-proof.sh <pr-number>`,
`./scripts/public-release-runtime-proof.sh <approved-release-tag>`,
`./scripts/public-release-publish-readiness.sh <approved-release-tag>` and
`./scripts/public-release-postpublish-proof.sh <approved-release-tag>`.

## OpenSpec

Active release-relevant changes validated in this release-readiness pass:

- `hide-canceled-subscription-accounts`
- `fix-runtime-release-repository`
- `harden-trusted-proxy-api-key-auth`
- `fix-anthropic-quota-selection-diagnostics`
- `fix-menubar-limit-status-sync`
- `require-beta-candidate-validation`
```

Desired repo metadata, matching the README header:

```text
Description:
ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints

Homepage:
https://github.com/aneym/agent-lb

Topics:
python oauth sqlalchemy dashboard load-balancer openai anthropic claude rate-limit api-proxy codex fastapi usage-tracking chatgpt opencode openclaw

Resources:
Homepage https://github.com/aneym/agent-lb
Repository https://github.com/aneym/agent-lb
Issues https://github.com/aneym/agent-lb/issues
Releases https://github.com/aneym/agent-lb/releases
Discussions https://github.com/aneym/agent-lb/discussions
Security https://github.com/aneym/agent-lb/security/advisories/new
```

Metadata commands:

```bash
gh repo edit aneym/agent-lb \
  --description "ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints" \
  --homepage "https://github.com/aneym/agent-lb"

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  /repos/aneym/agent-lb/topics \
  --input - <<'JSON'
{
  "names": [
    "python",
    "oauth",
    "sqlalchemy",
    "dashboard",
    "load-balancer",
    "openai",
    "anthropic",
    "claude",
    "rate-limit",
    "api-proxy",
    "codex",
    "fastapi",
    "usage-tracking",
    "chatgpt",
    "opencode",
    "openclaw"
  ]
}
JSON
```

Replacement prerelease notes for `v1.20.0-beta.3`, or for the next beta tag if
the dirty release-ready candidate is committed under a new tag:

```markdown
Beta prerelease for Agent LB public-release readiness.

Highlights:
- Refreshes the public README, package metadata, onboarding runbook, and agent skills around the `aneym/agent-lb` fork and ChatGPT + Claude account pooling.
- Regenerates dashboard, accounts, settings, login, and dark-mode screenshots.
- Hardens release-risk surfaces: subscription-usable account counts, trusted-proxy API-key bypass, runtime release links, Anthropic quota diagnostics, HTTP bridge compatibility, provider/default migration compatibility, and macOS menubar status sync.
- Fixes beta-prerelease reruns so existing prereleases still dispatch PyPI, Docker, Helm, and release-asset publishing, with release workflow concurrency scoped by tag.
- Tightens beta release-candidate validation so exactly one live upstream/account smoke checklist choice is accepted before tag or prerelease publication.
- Keeps behavior changes covered by OpenSpec and preserves release-please ownership of stable release promotion.

Validation:
- `uv run pytest -q` -> `3675 passed, 43 skipped, 4 warnings in 213.60s`
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make lint` -> proxy architecture checks and Ruff passed
- `cd frontend && bun run test && bun run screenshots && bun run lint && bun run build` -> Vitest, screenshots, lint, and build passed
- `cd clients/macos-menubar && swift test` -> `111 tests, 0 failures`
- `PATH="/Users/aneyman/repos/agent-lb/.venv/bin:$PATH" make package` -> wheel/sdist built and wheel assets verified
- `uvx --from twine==6.2.0 twine check dist/*` -> passed
- `./scripts/public-release-local-artifact-proof.sh v1.20.0-beta.3` -> passed on 2026-06-14T05:35:44Z after printing `localArtifactProofAt=2026-06-14T05:35:44Z`
- `uv run python -m scripts.verify_release_version --tag v1.20.0-beta.3` -> `pypi_version=1.20.0b3`
- `bash -n scripts/public-release-drift-scan.sh scripts/public-release-preflight.sh && ./scripts/public-release-drift-scan.sh` -> passed read-only public release drift scan
- `uv run pytest -q tests/unit/test_guard_beta_release.py` -> `10 passed`
- `uv run pytest -q tests/unit/test_public_release_docs.py` -> `79 passed`
- `uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py` -> `109 passed`
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:11:16Z after printing `snapshotAt=2026-06-14T06:11:16Z`, `snapshotOptionalFailures=5`, and `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; public artifacts remain unpublished/not visible
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T06:16:55Z with live snapshot `snapshotAt=2026-06-14T06:16:56Z`, local artifact proof `localArtifactProofAt=2026-06-14T06:16:58Z`, `106 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:16:56Z after printing `snapshotAt=2026-06-14T06:16:56Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:25:33Z after printing `snapshotAt=2026-06-14T06:25:33Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T06:25:32Z with live snapshot `snapshotAt=2026-06-14T06:25:33Z`, local artifact proof `localArtifactProofAt=2026-06-14T06:25:36Z`, `106 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:36:14Z after printing `snapshotAt=2026-06-14T06:36:14Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T06:36:13Z with live snapshot `snapshotAt=2026-06-14T06:36:14Z`, local artifact proof `localArtifactProofAt=2026-06-14T06:36:17Z`, `107 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot on 2026-06-14T06:43:07Z after adding release-state blockers; selected tag, prerelease flag, and draft status passed, while `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart` remained
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T06:44:35Z with live snapshot `snapshotAt=2026-06-14T06:44:36Z`, local artifact proof `localArtifactProofAt=2026-06-14T06:44:39Z`, `107 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:44:36Z after printing `snapshotAt=2026-06-14T06:44:36Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, and draft status passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T06:49:17Z with live snapshot `snapshotAt=2026-06-14T06:49:17Z`, local artifact proof `localArtifactProofAt=2026-06-14T06:49:20Z`, `107 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:49:17Z after printing `snapshotAt=2026-06-14T06:49:17Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T06:55:29Z with live snapshot `snapshotAt=2026-06-14T06:55:29Z`, local artifact proof `localArtifactProofAt=2026-06-14T06:55:32Z`, `107 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T06:55:29Z after printing `snapshotAt=2026-06-14T06:55:29Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T07:03:23Z with live snapshot `snapshotAt=2026-06-14T07:03:24Z`, local artifact proof `localArtifactProofAt=2026-06-14T07:03:27Z`, `108 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T07:03:24Z after printing `snapshotAt=2026-06-14T07:03:24Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T07:15:03Z with live snapshot `snapshotAt=2026-06-14T07:15:04Z`, local artifact proof `localArtifactProofAt=2026-06-14T07:15:07Z`, `108 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T07:15:04Z after printing `snapshotAt=2026-06-14T07:15:04Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T07:22:54Z with live snapshot `snapshotAt=2026-06-14T07:22:55Z`, local artifact proof `localArtifactProofAt=2026-06-14T07:22:57Z`, `108 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T07:22:55Z after printing `snapshotAt=2026-06-14T07:22:55Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot on 2026-06-14T07:35:29Z after release identity blocker hardening; selected tag, release title, public URL, published timestamp, prerelease flag, draft status, public visibility, private/archive state, and default branch passed; remaining blockers stayed `release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T07:37:07Z with live snapshot `snapshotAt=2026-06-14T07:37:08Z`, local artifact proof `localArtifactProofAt=2026-06-14T07:37:10Z`, `108 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T07:37:08Z after printing `snapshotAt=2026-06-14T07:37:08Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, release title, public URL, published timestamp, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T07:48:03Z with live snapshot `snapshotAt=2026-06-14T07:48:04Z`, local artifact proof `localArtifactProofAt=2026-06-14T07:48:07Z`, `108 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T07:48:04Z after printing `snapshotAt=2026-06-14T07:48:04Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, release title, public URL, published timestamp, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T07:56:47Z with live snapshot `snapshotAt=2026-06-14T07:56:48Z`, local artifact proof `localArtifactProofAt=2026-06-14T07:56:51Z`, `108 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace; the PyPI JSON probe now requires exact `agent_lb-1.20.0b3-py3-none-any.whl` and `agent_lb-1.20.0b3.tar.gz` filenames once PyPI is visible
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T07:56:48Z after printing `snapshotAt=2026-06-14T07:56:48Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, release title, public URL, published timestamp, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T08:07:12Z with live snapshot `snapshotAt=2026-06-14T08:07:12Z`, local artifact proof `localArtifactProofAt=2026-06-14T08:07:15Z`, `108 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the preflight on 2026-06-14T08:07:12Z after printing `snapshotAt=2026-06-14T08:07:12Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, release title, public URL, published timestamp, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed standalone read-only live blocker snapshot on 2026-06-14T08:34:06Z after printing `snapshotAt=2026-06-14T08:34:06Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; open PRs/runs stayed empty, the existing prerelease still has no assets and the older pricing/warmup body, hosted repo metadata is still stale/empty, PyPI remains 404, pip index still has no matching distribution, and GHCR image/chart manifests remain denied/not visible; no GitHub mutations were made
- `./scripts/public-release-preflight.sh v1.20.0-beta.3` -> passed read-only release preflight on 2026-06-14T08:38:25Z with live snapshot `snapshotAt=2026-06-14T08:38:26Z`, local artifact proof `localArtifactProofAt=2026-06-14T08:38:29Z`, `109 passed`, beta guard `10 passed`, `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and whitespace
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed read-only live blocker snapshot inside the full preflight on 2026-06-14T08:38:26Z after printing `snapshotAt=2026-06-14T08:38:26Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; selected tag, release title, public URL, published timestamp, prerelease flag, draft status, public visibility, private/archive state, and default branch passed
- `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` -> completed standalone read-only live blocker snapshot on 2026-06-14T08:50:46Z after printing `snapshotAt=2026-06-14T08:50:46Z`, `snapshotOptionalFailures=5`, `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`, and `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`; open PRs/runs stayed empty, the existing prerelease still has no assets and the older pricing/warmup body, hosted repo metadata is still stale/empty, PyPI remains 404, pip index still has no matching distribution, and GHCR image/chart manifests remain denied/not visible; no GitHub mutations were made
- `scripts/public-release-postpublish-proof.sh` diagnostics hardening on 2026-06-14T08:05:15Z -> the helper now prints the expected PyPI version and exact wheel/sdist filenames before the PyPI JSON gate; `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3` printed `postpublishProofAt=2026-06-14T08:06:34Z`, `expectedPypiVersion=1.20.0b3`, `expectedPypiWheelAsset=agent_lb-1.20.0b3-py3-none-any.whl`, and `expectedPypiSdistAsset=agent_lb-1.20.0b3.tar.gz`, then exited expected non-zero at the still-unpublished PyPI JSON 404 (`curl: (56)`); `bash -n scripts/public-release-postpublish-proof.sh`, `uv run pytest -q tests/unit/test_public_release_docs.py` (`78 passed`), the release/docs/Kubernetes slice (`108 passed`), strict OpenSpec validation for `require-beta-candidate-validation`, main specs (`30 passed, 0 failed`), Ruff, and whitespace passed
- PyPI identity proof hardening on 2026-06-14T08:20:38Z -> post-publish proof now prints and verifies the expected PyPI summary and project URLs; `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3` printed `expectedPypiSummary=ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints` plus `expectedPypiProjectUrls={"Homepage":"https://github.com/aneym/agent-lb","Repository":"https://github.com/aneym/agent-lb","Issues":"https://github.com/aneym/agent-lb/issues","Releases":"https://github.com/aneym/agent-lb/releases"}`, then exited expected non-zero at the still-unpublished PyPI JSON 404 (`curl: (56)`); `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` printed `snapshotAt=2026-06-14T08:20:38Z`, kept `snapshotOptionalFailures=5` and the same blocker names, and showed the PyPI JSON predicate now checks version, summary, project URLs, and exact wheel/sdist filenames
- PyPI identity proof verification on 2026-06-14T08:21:36Z -> shell syntax passed; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `78 passed`; release-version/public-doc/K8s slice -> `108 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- OpenSpec contract alignment on 2026-06-14T08:26:07Z -> active release-management delta now explicitly pins the post-publish PyPI summary/project-URL/exact wheel-sdist identity checks, live-snapshot PyPI identity visibility, publish-readiness local-main evidence, and PR-head `headRefOid`/Codex `head=` SHA matching contract; `uv run pytest -q tests/unit/test_public_release_docs.py` -> `79 passed`; release-version/public-doc/K8s slice -> `109 passed`; `require-beta-candidate-validation` strict validation -> valid; main specs -> `30 passed, 0 failed`; Ruff and whitespace -> clean
- `./scripts/public-release-runtime-proof.sh v1.20.0-beta.3` -> expected non-zero before approved restart/reinstall on 2026-06-14T07:19:03Z after printing `runtimeProofAt=2026-06-14T07:19:03Z` (`rc=1`; health passed, release metadata parsed, runtime assertion returned `false` because the healthy daemon still serves the old upstream release URL)
- `./scripts/validate-active-openspec-changes.sh` -> `validated 54 active changes`
- `uv run python -m json.tool .agents/skills/skill-rules.json` -> passed
- `uv run python -m json.tool .agents/skills/agent-lb-account-operator/account-profiles.example.json` -> passed
- Workflow YAML parse for `.github/workflows/publish-beta-release.yml` and `.github/workflows/release.yml` -> passed
- `npx --yes @fission-ai/openspec@latest validate --specs` -> `30 passed, 0 failed`

Known caveat:
- GitHub/PyPI/GHCR publication is approval-gated. Local package artifacts are ready, but no public PyPI distribution, GHCR image, chart package, or release asset is visible until the release workflow/publish step runs successfully.
```

Release-notes command after saving the body above to a temp file:

```bash
gh release edit v1.20.0-beta.3 \
  --repo aneym/agent-lb \
  --title "Release v1.20.0-beta.3" \
  --notes-file /tmp/agent-lb-v1.20.0-beta.3-notes.md \
  --prerelease
```

Public artifact options after approval:

```bash
# Preflight: the selected tag must resolve to the approved candidate commit, and
# the release-ready tree must not depend on local-only dirty files.
./scripts/public-release-publish-readiness.sh <approved-release-tag>

# Only after the candidate is committed/pushed/tagged. Use v1.20.0-beta.3 only
# if that existing prerelease tag is explicitly approved and points at the
# candidate commit; otherwise use the new beta tag.
gh workflow run release.yml --repo aneym/agent-lb --ref <approved-release-tag> -f tag=<approved-release-tag>

# Watch the triggered workflow and inspect failure logs before retrying.
gh run list --repo aneym/agent-lb --workflow release.yml --limit 5
gh run watch --repo aneym/agent-lb <run-id> --exit-status
```

Expected post-publish proof:

The preferred single-command form is
`./scripts/public-release-postpublish-proof.sh <approved-release-tag>`; the
expanded command list below is the script contract.

```bash
expected_pypi_version="1.20.0b3"
expected_pypi_wheel_asset="agent_lb-1.20.0b3-py3-none-any.whl"
expected_pypi_sdist_asset="agent_lb-1.20.0b3.tar.gz"
echo "expectedPypiVersion=${expected_pypi_version}"
echo "expectedPypiWheelAsset=${expected_pypi_wheel_asset}"
echo "expectedPypiSdistAsset=${expected_pypi_sdist_asset}"
pypi_json="$(curl -fsS https://pypi.org/pypi/agent-lb/json)"
printf '%s\n' "${pypi_json}" \
  | jq -e \
      --arg pypi_version "${expected_pypi_version}" \
      --arg wheel_asset "${expected_pypi_wheel_asset}" \
      --arg sdist_asset "${expected_pypi_sdist_asset}" \
      '
        .info.version == $pypi_version
        and ([.releases[$pypi_version][]?.filename] | index($wheel_asset) != null and index($sdist_asset) != null)
      '
python3 -m pip index versions agent-lb | rg --fixed-strings '1.20.0b3'
docker manifest inspect ghcr.io/aneym/agent-lb:1.20.0-beta.3
docker manifest inspect ghcr.io/aneym/agent-lb:beta
docker manifest inspect ghcr.io/aneym/charts/agent-lb:1.20.0-beta.3
EXPECTED_DESCRIPTION="ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints"
EXPECTED_HOMEPAGE="https://github.com/aneym/agent-lb"
EXPECTED_TOPICS_JSON='["python","oauth","sqlalchemy","dashboard","load-balancer","openai","anthropic","claude","rate-limit","api-proxy","codex","fastapi","usage-tracking","chatgpt","opencode","openclaw"]'
gh repo view aneym/agent-lb --json description,homepageUrl,repositoryTopics,isArchived,isPrivate,visibility,url,defaultBranchRef \
  | jq -e \
      --arg expected_description "${EXPECTED_DESCRIPTION}" \
      --arg expected_homepage "${EXPECTED_HOMEPAGE}" \
      --argjson expected_topics "${EXPECTED_TOPICS_JSON}" \
      '
        def topic_name:
          if type == "string" then .
          elif has("name") then .name
          elif has("topic") then .topic.name
          else empty
          end;
        .description == $expected_description
        and .homepageUrl == $expected_homepage
        and .visibility == "PUBLIC"
        and .isPrivate == false
        and .isArchived == false
        and .defaultBranchRef.name == "main"
        and ([.repositoryTopics[]? | topic_name] | sort) == ($expected_topics | sort)
      '
expected_prerelease=true
expected_release_url="https://github.com/aneym/agent-lb/releases/tag/v1.20.0-beta.3"
expected_release_name="Release v1.20.0-beta.3"
expected_wheel_asset="agent_lb-1.20.0b3-py3-none-any.whl"
expected_sdist_asset="agent_lb-1.20.0b3.tar.gz"
gh release view v1.20.0-beta.3 --repo aneym/agent-lb --json tagName,name,assets,isPrerelease,isDraft,publishedAt,url,body \
  | jq -e \
      --arg expected_tag "v1.20.0-beta.3" \
      --arg expected_name "${expected_release_name}" \
      --arg expected_url "${expected_release_url}" \
      --argjson expected_prerelease "${expected_prerelease}" \
      --arg wheel_asset "${expected_wheel_asset}" \
      --arg sdist_asset "${expected_sdist_asset}" \
      '
        .tagName == $expected_tag
        and .name == $expected_name
        and .url == $expected_url
        and ((.publishedAt // "") != "")
        and .isPrerelease == $expected_prerelease
        and .isDraft == false
        and ([.assets[]?.name] | index($wheel_asset) != null and index($sdist_asset) != null)
        and (.body | contains("Beta prerelease for Agent LB public-release readiness."))
        and ((.body | contains("duplicate column name")) | not)
        and ((.body | contains("tests/integration/test_migrations.py currently fails")) | not)
      '
```

## Recommended Next Steps

1. Before any publish/release mutation, get explicit approval for the commit,
   push, tag, and account-visible GitHub actions. The current release-ready tree
   includes dirty/untracked local paths; the existing `v1.20.0-beta.3` tag can
   only publish committed contents.
2. Before any publish/release mutation, rerun a final approval-scoped diff
   check if the tree changes again. The latest local audit already covered
   subscription-hidden accounts, account-summary subscription status
   normalization, trusted-proxy API-key bypass, runtime release links,
   Anthropic quota diagnostics, menubar limit status sync, HTTP bridge
   compatibility, provider/default migration compatibility, and beta install
   docs for published artifacts, including Helm install/upgrade pins and
   release CODEOWNERS for the public fork owner.
3. With explicit user approval, update the live GitHub repo description,
   homepage, and topics to match the refreshed README/`pyproject.toml`; keep the
   README resource list aligned with that public metadata.
4. If package/container availability is part of the public-release bar, publish
   or attach artifacts only after user approval and only from a release tag that
   points at the committed candidate. Local sdist/wheel artifacts are ready, but
   PyPI/GHCR/release assets are not publicly visible as of the
   2026-06-14T08:50:46Z standalone snapshot (`snapshotOptionalFailures=5`;
   `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
   `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`). The staged
   publish-readiness guard fails closed when the tag does not point at `HEAD`,
   the checkout is not local `main` at `HEAD`, the working tree is dirty, or
   returned current-head `main` workflow evidence is missing/non-green;
   the staged post-publish proof script fails closed on wrong PyPI/pip-index
   versions, missing GHCR manifests, missing Helm chart package, stale public
   repository metadata, or a GitHub prerelease without the exact wheel/sdist
   assets and the replacement release body. The PR/contributor release-proof gates
   now require approval-gated publication work to name those commands plus the
   local artifact proof and PR-head proof helpers.
5. Use the commit/PR readiness preflight in this approval packet before asking
   for or performing any commit, push, tag, PR, release, or publication mutation.
6. Treat the running local launchd service as pre-candidate runtime evidence
   until it is restarted/reinstalled from the approved candidate. It is healthy
   at `http://127.0.0.1:2455/health` and reports `currentVersion`
   `1.20.0-beta.3`, but
   `http://127.0.0.1:2455/api/runtime/version` returned the old upstream
   `releaseUrl` `https://github.com/Soju06/agent-lb/releases/latest` with
   response `checkedAt` `2026-06-14T04:35:55.088884Z` during the earlier
   read-only check; a 2026-06-14T07:19:03Z runtime-proof refresh still failed
   the runtime-version assertion while health passed. No restart was performed
   because the service is healthy and restarts are approval-gated. After
   approval, rerun
   `./scripts/public-release-runtime-proof.sh v1.20.0-beta.3`; it is expected
   non-zero until the restarted/reinstalled daemon serves the fork release URL.

## Known Remaining Risk

Local tests, lint, frontend build, menubar tests, screenshots, active OpenSpec
changes, all specs, beta install docs, README client matrix checks, agent skill
activation checks, and local package artifacts are current and green. The
public-release docs suite now pins this exact remaining-risk boundary. The
active release-management spec now requires this completion boundary to stay
explicit while any release proof remains approval-gated or unverified. The
remaining unchecked OpenSpec tasks are PR/CI/Codex-review gates that need a
committed PR head. The release is still not complete because the release-ready
tree is not committed and tagged, live GitHub metadata is stale, public
package/container assets are not visible, the healthy live launchd daemon still
serves a pre-candidate runtime release URL until approved restart/reinstall
(`./scripts/public-release-runtime-proof.sh v1.20.0-beta.3` currently fails
closed with `rc=1`), and
publishing or release mutation still requires explicit user approval.
