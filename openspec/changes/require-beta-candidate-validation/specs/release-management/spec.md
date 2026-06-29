## MODIFIED Requirements

### Requirement: Beta releases are prepared through release PRs

Beta releases SHALL be prepared by an automatically maintained pull request against `main` that updates the release-managed version files to `X.Y.Z-beta.N`. The `uv.lock` package entry SHALL use the PEP 440-normalized prerelease spelling (`X.Y.ZbN`) for the same logical beta version, while other release-managed files SHALL use `X.Y.Z-beta.N`. The beta preparation flow SHALL run after release-please completes and after pushes to `main`, SHALL derive `X.Y.Z` from the open release-please PR branch, and SHALL do nothing when there is no open release-please PR. Beta release PRs SHALL NOT update `.github/release-please-manifest.json` because stable version ownership remains with release-please.

#### Scenario: automation syncs the next beta from the release-please PR

- **GIVEN** release-please has opened or updated `release-please--branches--main` with `pyproject.toml` version `1.19.0`
- **WHEN** the beta PR sync workflow runs
- **THEN** it creates or updates a pull request that sets release-managed files to `1.19.0-beta.N`
- **AND** it sets the `uv.lock` package entry to the PEP 440-normalized spelling `1.19.0bN`
- **AND** `N` is one higher than the highest existing `v1.19.0-beta.N` tag
- **AND** `.github/release-please-manifest.json` remains unchanged

#### Scenario: automation is idle without a release-please PR

- **GIVEN** there is no open release-please PR targeting `main`
- **WHEN** the beta PR sync workflow runs
- **THEN** it exits without creating a beta release pull request

#### Scenario: automation ignores forked release-please branch names

- **GIVEN** a fork has an open pull request whose head branch is named `release-please--branches--main`
- **WHEN** the beta PR sync workflow looks for the release-please PR
- **THEN** it ignores that pull request unless the head repository owner is the canonical repository owner
- **AND** it requests enough open pull requests to avoid missing the canonical release-please PR during high-PR-volume periods

#### Scenario: merged beta release already covers main

- **GIVEN** tag `v1.19.0-beta.1` points to `HEAD`
- **AND** release-managed files resolve to `1.19.0-beta.1`
- **AND** the `uv.lock` package entry contains `1.19.0b1`
- **WHEN** the beta PR sync workflow runs for base version `1.19.0`
- **THEN** it exits without creating `1.19.0-beta.2`

#### Scenario: automation-generated beta PR starts unvalidated

- **GIVEN** the beta PR sync workflow creates or updates `release/beta-1.20.0-beta.3`
- **WHEN** it writes the pull request body
- **THEN** the body includes a `Release-candidate validation` section
- **AND** the section records the exact beta PR head SHA as the validated candidate placeholder
- **AND** backend, frontend, wheel/package, Docker/container, and live upstream/account smoke checklist items start unchecked

### Requirement: Merged beta release PRs publish GitHub prereleases

When a pull request from the canonical repository's `release/beta-*` branch is merged into `main`, the release automation SHALL require `RELEASE_PLEASE_TOKEN` rather than falling back to `GITHUB_TOKEN`, verify that all release-managed version files agree on the same logical beta version, require release-candidate validation evidence for the exact merged pull request head SHA, require exactly one live upstream/account smoke checklist choice, verify that the published merge commit tree matches that validated head tree, create the matching `vX.Y.Z-beta.N` tag at the merge commit, and publish a GitHub prerelease for that tag. Re-running the workflow after the tag already exists SHALL be safe, SHALL NOT create a second tag, and SHALL dispatch the release publishing workflow for the existing tag so PyPI, Docker, Helm, and release-asset publication still run. The release publishing workflow SHALL scope concurrency by the selected release tag for both GitHub release events and manual dispatches.

#### Scenario: beta PR merge publishes a prerelease tag

- **GIVEN** a merged pull request from `release/beta-1.19.0-beta.1`
- **AND** release-managed files resolve to `1.19.0-beta.1`
- **AND** the `uv.lock` package entry contains `1.19.0b1`
- **AND** the pull request body contains checked release-candidate validation evidence for the exact merged pull request head SHA
- **AND** the merge commit tree matches the validated pull request head tree
- **AND** `RELEASE_PLEASE_TOKEN` is configured
- **WHEN** the beta publish workflow runs
- **THEN** it creates tag `v1.19.0-beta.1` at the merge commit
- **AND** it creates a GitHub prerelease for `v1.19.0-beta.1`

#### Scenario: existing beta prerelease still publishes artifacts

- **GIVEN** a merged pull request from `release/beta-1.19.0-beta.1`
- **AND** tag `v1.19.0-beta.1` already points to the merged commit
- **AND** release-managed files resolve to `1.19.0-beta.1`
- **AND** the pull request body contains checked release-candidate validation evidence for the exact merged pull request head SHA
- **WHEN** the beta publish workflow runs
- **THEN** it updates the existing GitHub prerelease notes without creating a duplicate tag
- **AND** it dispatches the release publishing workflow for `v1.19.0-beta.1`
- **AND** PyPI, Docker, Helm, and release-asset publication are handled by that release publishing workflow
- **AND** the release publishing workflow concurrency key is scoped to `v1.19.0-beta.1`

#### Scenario: inconsistent release metadata is blocked

- **GIVEN** a pull request changes one or more release-managed version files
- **AND** the release-managed files do not resolve to the same logical version
- **WHEN** the CI beta release guard runs
- **THEN** it fails before deciding whether the change is stable or beta
- **AND** it reports the mismatched release-managed file versions

#### Scenario: non-canonical beta metadata PR is blocked

- **GIVEN** a pull request changes release-managed files to `1.20.0-beta.3`
- **AND** the pull request head branch is `fix/pr-938-release-ci`
- **WHEN** the CI beta release guard runs
- **THEN** it fails before the pull request can satisfy the required CI rollup
- **AND** it reports that the expected head branch is `release/beta-1.20.0-beta.3`

#### Scenario: forked beta metadata PR is blocked

- **GIVEN** a pull request changes release-managed files to `1.20.0-beta.3`
- **AND** the pull request head branch is `release/beta-1.20.0-beta.3`
- **BUT** the pull request head repository is a fork rather than the canonical repository
- **WHEN** the CI beta release guard or beta publish guard runs
- **THEN** it fails before the pull request can merge or publish
- **AND** it reports the expected and actual head repositories

#### Scenario: beta publish refuses missing validation evidence

- **GIVEN** a merged pull request from `release/beta-1.20.0-beta.3`
- **AND** release-managed files resolve to `1.20.0-beta.3`
- **AND** the `uv.lock` package entry contains `1.20.0b3`
- **BUT** the pull request body lacks checked release-candidate validation evidence for the exact pull request head SHA
- **WHEN** the beta publish workflow runs
- **THEN** it fails before creating the `v1.20.0-beta.3` tag
- **AND** it does not create a GitHub prerelease or publish artifacts

#### Scenario: beta publish refuses contradictory live smoke evidence

- **GIVEN** a merged pull request from `release/beta-1.20.0-beta.3`
- **AND** release-managed files resolve to `1.20.0-beta.3`
- **AND** the pull request body contains checked release-candidate validation evidence for the exact pull request head SHA
- **BUT** both `Live upstream/account smoke` and `Live upstream/account smoke not required` are checked
- **WHEN** the beta publish workflow runs
- **THEN** it fails before creating the `v1.20.0-beta.3` tag
- **AND** it reports that exactly one live upstream/account smoke item must be checked

#### Scenario: beta publish refuses a stale validated tree

- **GIVEN** a merged pull request from `release/beta-1.20.0-beta.3`
- **AND** the pull request body contains checked release-candidate validation evidence for the pull request head SHA
- **BUT** the merge commit tree differs from the validated pull request head tree
- **WHEN** the beta publish workflow runs
- **THEN** it fails before creating the `v1.20.0-beta.3` tag
- **AND** it reports that the beta PR must be updated onto the final base and revalidated

## ADDED Requirements

### Requirement: Public release approval preflight validates active OpenSpec changes

The public release approval preflight SHALL run a strict validation pass for every active OpenSpec change folder before any commit, PR, tag, release, publication, or account-visible GitHub mutation. The pass MUST exclude archived changes, MUST validate each active change with the npm-distributed `@fission-ai/openspec` CLI using `--strict`, and MUST fail if any active change has no valid spec delta or invalid requirement text.

The public release approval preflight SHALL provide a repo-local script entrypoint that runs the read-only release readiness checks before any commit, PR, tag, release, publication, or account-visible GitHub mutation. The script MUST require an approved release tag, MUST print a UTC preflight timestamp, MUST verify the tag and release-channel mapping, MUST check live PR and workflow state using read-only GitHub CLI commands, MUST run the live public blocker snapshot, release helper script syntax checks, release-version, public-doc, beta-guard, active OpenSpec, main spec, Ruff, and whitespace checks, and MUST NOT include commands that commit, push, create PRs, edit repository metadata, edit releases, dispatch workflows, or call mutating GitHub API endpoints.

The public release process SHALL provide a repo-local post-publish proof script that verifies public artifacts and repository metadata after an approved release publication. The script MUST be read-only, MUST print a UTC post-publish proof timestamp, MUST verify release tag and release-channel mapping, MUST verify the normalized PyPI version, public package summary, project URLs, and exact wheel/sdist filenames through PyPI JSON, MUST verify the normalized PyPI version through pip index output, MUST verify the GHCR image tag and channel alias, MUST verify the GHCR Helm chart tag, MUST verify GitHub repository description, homepage, public visibility, default branch, and topic set, MUST verify GitHub release title, public release URL, non-empty published timestamp, draft/prerelease state, attached assets, and replacement release body freshness, and MUST fail non-zero when any expected public artifact, metadata value, or release note is missing or mismatched.

The public release process SHALL provide a repo-local publish-readiness script that fails closed before publication when the checkout is not local `main`, local `main` does not point at `HEAD`, the selected release tag does not point at `HEAD`, or the working tree contains dirty or untracked paths. The script MUST be read-only, MUST require an approved release tag, MUST print a UTC publish-readiness timestamp, MUST print the current branch and local `main` SHA evidence, MUST verify release tag and release-channel mapping before dirty-tree publication blocking, MUST report the dirty-path count when dirty or untracked paths block publication, MUST check live PR and workflow state using read-only GitHub CLI commands after local eligibility passes, MUST fail non-zero when the current `HEAD` has no returned `main` workflow run evidence, MUST fail non-zero when any returned current-head `main` workflow run is not completed with a successful, skipped, or neutral conclusion, and MUST NOT include commands that commit, push, create PRs, edit repository metadata, edit releases, dispatch workflows, or call mutating GitHub API endpoints.

The public release process SHALL provide a repo-local live snapshot script that refreshes the current public release blocker state before asking for approval or reporting release readiness. The script MUST be read-only, MUST require an approved release tag, MUST print a UTC snapshot timestamp, MUST verify release tag and release-channel mapping, MUST report live PR, workflow, release identity/state/assets/body, repository metadata, PyPI version/summary/project-URL/file visibility, pip-index, GHCR image, GHCR channel alias, and GHCR Helm chart visibility, MUST continue reporting when optional public artifacts are not yet visible, MUST summarize the count of optional read-only check failures before completion, and MUST NOT include commands that commit, push, create PRs, edit repository metadata, edit releases, dispatch workflows, or call mutating GitHub API endpoints.

The public release process SHALL provide a repo-local PR-head proof script that verifies merge readiness after a release pull request exists. The script MUST be read-only, MUST require a pull request number, MUST print a UTC PR-head proof timestamp, MUST verify the pull request is open, non-draft, targets `main`, uses the canonical repository owner for its head branch, has clean mergeability, carries the trusted `🤖 codex: ok` label without the `🤖 codex: needs work` label, and has passing required checks including `CI Required`. The script MUST print the pull request `headRefOid` as full and 12-character short SHA evidence, MUST verify current-head Codex classifier output proves the same short head SHA, successful checks, clean merge state, and a clean Codex review, and MUST fail non-zero when any required PR-head proof is missing or stale.

The public release approval preflight SHALL verify local package artifacts for the approved release tag before publication. The local artifact proof MUST be read-only, MUST print a UTC local-artifact proof timestamp, MUST derive the expected PyPI-normalized version from the approved release tag, MUST require the matching wheel and sdist under `dist/`, MUST verify the sdist README matches the repository README, MUST reject dev-only top-level package contents, MUST verify wheel metadata for the public package name, version, summary, maintainer, and beta classifier, and MUST run metadata validation for the selected artifacts.

The public release process SHALL provide a repo-local runtime proof script that verifies the running local daemon after an approved restart or reinstall. The script MUST be read-only, MUST require an approved release tag, MUST print a UTC runtime proof timestamp, MUST verify release tag and release-channel mapping, MUST verify local daemon health, MUST verify `/api/runtime/version` reports the approved version and approved fork release URL with no pending update, MUST fail non-zero when the running daemon still serves a stale version or release URL, and MUST NOT restart, reinstall, commit, push, create pull requests, edit repository metadata, edit releases, dispatch workflows, or call mutating GitHub API endpoints.

The public release screenshot capture SHALL run against the repo-built Agent LB frontend preview server. The screenshot harness MUST use a dedicated preview URL owned by the Playwright web server, MUST NOT reuse an already-running server on the screenshot port, and MUST navigate capture scenes through Playwright's configured base URL instead of hard-coding a localhost URL that may belong to another application.

The public release handoff SHALL keep the remaining-risk and completion boundary explicit until the release is actually published and verified. The handoff MUST NOT describe a release as complete while any commit/PR, PR-head CI/Codex-review, public repository metadata, package/container/chart artifact, release-asset/body, runtime restart/reinstall, or publication action remains approval-gated or unverified. The handoff MUST name the remaining proof command or approval gate for each such blocker.

#### Scenario: approval preflight catches an active change without a valid spec delta

- **GIVEN** an active change folder under `openspec/changes/` lacks a valid delta spec
- **WHEN** the public release approval preflight runs
- **THEN** strict active-change validation fails before any public release mutation is performed

#### Scenario: approval preflight validates all active changes before release mutation

- **GIVEN** every non-archived folder under `openspec/changes/` contains a valid delta spec
- **WHEN** the public release approval preflight runs
- **THEN** it validates each active change with `@fission-ai/openspec` and `--strict`
- **AND** archived changes under `openspec/changes/archive/` are excluded from the active sweep

#### Scenario: scripted approval preflight remains read-only

- **GIVEN** a release operator runs the repo-local public release preflight script with an approved beta tag
- **WHEN** the script performs release readiness checks
- **THEN** it verifies local status, local diff, approved tag mapping, live PR state, live workflow state, dependency lock, release helper script syntax, release-version regressions, public docs regressions, beta guard regressions, active OpenSpec changes, main specs, Ruff, and whitespace
- **AND** it does not commit, push, create pull requests, edit repository metadata, edit releases, dispatch workflows, or call mutating GitHub API endpoints

#### Scenario: post-publish proof fails before artifacts or metadata are correct

- **GIVEN** a release operator runs the repo-local post-publish proof script with an approved beta tag
- **AND** PyPI version, PyPI package summary, PyPI project URLs, PyPI wheel/sdist filenames, pip index, GHCR image, GHCR chart, GitHub release assets, repository metadata, or replacement release notes are not visible or correct yet
- **WHEN** the script checks public artifact and metadata availability
- **THEN** it exits non-zero before reporting the release publication complete

#### Scenario: live snapshot reports unpublished artifacts without mutating state

- **GIVEN** a release operator runs the repo-local live snapshot script with an approved beta tag before publication
- **AND** PyPI version/summary/project-URL/file details, pip index, GHCR image, GHCR chart, or GitHub release assets are not visible yet
- **WHEN** the script checks public release blocker state
- **THEN** it reports the current live GitHub and artifact visibility
- **AND** it continues to later read-only checks instead of stopping at the first missing public artifact
- **AND** it prints a count of optional read-only check failures before completion
- **AND** it does not commit, push, create pull requests, edit repository metadata, edit releases, dispatch workflows, or call mutating GitHub API endpoints

#### Scenario: handoff remains incomplete while approval-gated blockers remain

- **GIVEN** the release-ready tree has not been committed and tagged
- **OR** PR-head CI/Codex review has not been proven on a release pull request
- **OR** public repository metadata, package/container/chart artifacts, release assets/body, runtime restart/reinstall, or publication proof is still approval-gated or unverified
- **WHEN** the release handoff describes the current public release state
- **THEN** it states that the release is still not complete
- **AND** it names the remaining proof command or approval gate before calling the release ready for publication

#### Scenario: PR-head proof fails until current-head gates are green

- **GIVEN** a release operator runs the repo-local PR-head proof script for a release pull request
- **WHEN** the pull request is draft, targets a non-main base, uses a non-canonical head owner, lacks `headRefOid`, has stale mergeability, lacks the trusted Codex ok label, has the trusted Codex needs-work label, lacks passing required checks, lacks `CI Required`, lacks current-head clean Codex classifier output, or has Codex classifier output for a different head SHA
- **THEN** the script exits non-zero before the release can be called PR-ready

#### Scenario: screenshot capture refuses stale local preview servers

- **GIVEN** another application is already listening on a common local preview port
- **WHEN** a release operator runs the public screenshot capture
- **THEN** Playwright starts the repo-built Agent LB preview server on its dedicated screenshot URL
- **AND** capture scenes navigate through the configured Playwright base URL
- **AND** the harness does not reuse the unrelated running server

#### Scenario: post-publish proof passes only after all public artifacts and metadata are visible

- **GIVEN** a release operator runs the repo-local post-publish proof script with an approved beta tag after publication
- **WHEN** the PyPI JSON version, package summary, project URLs, exact wheel/sdist filenames, pip index version, GHCR image tag, GHCR channel alias, GHCR chart tag, GitHub repository metadata, GitHub release title, public release URL, published timestamp, release assets, and replacement release body all match the approved tag and public release target
- **THEN** the script exits successfully

#### Scenario: approval preflight catches stale local package artifacts

- **GIVEN** a release operator has an approved release tag
- **AND** `dist/` contains a stale sdist whose README does not match the repository README
- **WHEN** the public release approval preflight runs
- **THEN** the local artifact proof fails before any publication or account-visible GitHub mutation is performed

#### Scenario: runtime proof fails until the approved daemon serves the fork release URL

- **GIVEN** a release operator has an approved release tag
- **AND** the running local daemon is healthy but still reports an old runtime release URL
- **WHEN** the release operator runs the repo-local runtime proof script before the approved restart or reinstall has taken effect
- **THEN** the runtime proof exits non-zero before the runtime release-link blocker can be closed
- **AND** it does not restart or reinstall the daemon or perform any GitHub mutation

#### Scenario: runtime proof passes after approved restart or reinstall

- **GIVEN** a release operator has an approved release tag
- **AND** the local daemon has been restarted or reinstalled from the approved candidate
- **WHEN** the release operator runs the repo-local runtime proof script
- **THEN** it verifies daemon health, approved runtime version, no pending update, and the approved fork release URL
- **AND** it exits successfully

#### Scenario: publish readiness blocks local-only release overlays

- **GIVEN** the selected approved release tag points at `HEAD`
- **BUT** the working tree contains dirty or untracked paths
- **WHEN** a release operator runs the repo-local publish-readiness script before publication
- **THEN** the script exits non-zero before any publication command is run
- **AND** it verifies the release tag and release-channel mapping before reporting the dirty tree
- **AND** it reports the dirty-path count
- **AND** it reports that the working tree must be committed or intentionally excluded before publishing

#### Scenario: publish readiness blocks tag drift

- **GIVEN** the selected approved release tag does not point at `HEAD`
- **WHEN** a release operator runs the repo-local publish-readiness script before publication
- **THEN** the script exits non-zero before any publication command is run
- **AND** it reports that the release tag does not point at `HEAD`

#### Scenario: publish readiness blocks non-main checkout drift

- **GIVEN** the selected approved release tag points at `HEAD`
- **BUT** the checkout is detached, on a branch other than `main`, or local `main` does not point at `HEAD`
- **WHEN** a release operator runs the repo-local publish-readiness script before publication
- **THEN** the script exits non-zero before any publication command is run
- **AND** it reports the current branch and local `main` SHA evidence
- **AND** it reports that publish readiness must run from local `main` at `HEAD`

#### Scenario: publish readiness blocks missing or non-green current-head runs

- **GIVEN** the selected approved release tag points at `HEAD`
- **AND** the working tree is clean
- **WHEN** the read-only live workflow probe returns no `main` runs for the current `HEAD`, a current-head run that is not completed, or a current-head run with a conclusion other than success, skipped, or neutral
- **THEN** the script exits non-zero before any publication command is run
- **AND** it reports the current-head workflow-run evidence gap or the non-green run details
