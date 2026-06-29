## 1. Pull request guard

- [x] 1.1 Detect PRs that change release-managed version files to a beta version.
- [x] 1.2 Fail release-managed version changes unless all managed files agree
      before deciding whether the change targets a beta version.
- [x] 1.3 Fail beta PRs unless the head branch is the canonical
      `release/beta-X.Y.Z-beta.N` branch for the target version.
- [x] 1.4 Treat `uv.lock` prerelease versions as PEP 440-normalized spellings
      of the same logical beta release during release-managed version checks.
- [x] 1.5 Fail those PRs unless the head repository is the canonical repository.
- [x] 1.6 Fail those PRs unless the PR body records checked validation evidence
      for the exact PR head SHA.
- [x] 1.7 Re-run the guard on PR body edits so validation checklist updates are
      reflected before merge.
- [x] 1.8 Fail those PRs when both mutually exclusive live upstream/account smoke
      checklist choices are checked.

## 2. Publish guard

- [x] 2.1 Re-check the canonical branch and validation evidence in
      `publish-beta-release.yml` before creating a tag or GitHub prerelease.
- [x] 2.2 Require the published merge commit tree to match the validated PR head
      tree before tag creation, so `main` cannot advance after validation.
- [x] 2.3 Reject merged beta PRs whose head repository is not the canonical
      repository, even if the fork used the canonical beta branch name.
- [x] 2.4 Keep the publish check stdlib-only so it can run before dependency
      installation or artifact publishing.
- [x] 2.5 Dispatch the release publishing workflow when the matching GitHub
      prerelease already exists, so reruns still publish PyPI, Docker, Helm,
      and release assets.
- [x] 2.6 Scope release publishing workflow concurrency by the selected tag for
      both release events and manual dispatches.

## 3. Beta PR template

- [x] 3.1 Add an unchecked release-candidate validation checklist to
      automation-generated beta PR bodies.
- [x] 3.2 Include the exact candidate SHA in that template so stale validation is
      detected when the release PR branch changes.

## 4. Verification

- [x] 4.1 Add unit tests for non-canonical branch rejection, forked canonical
      branch rejection, missing evidence, stale SHA evidence, and passing
      validated canonical PRs.
- [x] 4.2 Run focused ruff and pytest checks for the guard implementation.
- [x] 4.3 Validate the OpenSpec change strictly.
- [x] 4.4 Add and run public release docs coverage for the existing-prerelease
      artifact-dispatch branch.
- [x] 4.5 Add and run public release docs coverage for release workflow
      tag-scoped concurrency.
- [x] 4.6 Add and run guard coverage for mutually exclusive live upstream/account
      smoke checklist choices.
- [ ] 4.7 Confirm GitHub CI and Codex review on the PR head.
  - Pending until the release-ready tree is committed and a PR exists. Read-only
    refresh on 2026-06-14T07:20:58Z: `gh pr list --repo aneym/agent-lb
    --state open` returned `[]`, and `gh run list --repo aneym/agent-lb
    --branch main --limit 10` returned `[]`; `git status --porcelain | wc -l`
    reported `167` dirty/untracked paths. Once a PR exists, run
    `./scripts/public-release-pr-head-proof.sh <pr-number>`.
- [x] 4.8 Add and run approval-preflight active OpenSpec validation coverage.
  - `./scripts/validate-active-openspec-changes.sh` on 2026-06-14T02:13:06Z
    reported `validated 54 active changes`; public-release docs regression
    coverage now pins the script and approval preflight command.
- [x] 4.9 Add and run repo-local public release preflight script coverage.
  - `./scripts/public-release-preflight.sh v1.20.0-beta.3` passed on
    2026-06-14T04:51:13Z, including read-only live PR/run checks, locked
    dependency verification, release helper syntax checks, release
    docs/version/Kubernetes tests, local artifact proof, beta guard,
    release-version verification, active OpenSpec sweep, main spec validation,
    Ruff, and whitespace.
  - 2026-06-14T05:31:47Z refresh: the same one-command preflight passed again
    after the preflight script began printing `preflightAt=2026-06-14T05:31:47Z`
    and the publish-readiness guard began verifying tag/channel mapping before
    the dirty-tree block.
  - 2026-06-14T05:43:20Z refresh: the same one-command preflight passed again
    after printing `preflightAt=2026-06-14T05:43:20Z`, including read-only
    PR/run checks (`[]`/`[]`), locked dependency verification, helper syntax
    checks, drift scan, local artifact proof, release-version/public-doc/K8s
    tests, beta guard, release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T05:47:09Z refresh after the PR draft cleanup: the same
    one-command preflight passed again after printing
    `preflightAt=2026-06-14T05:47:09Z`, including read-only PR/run checks
    (`[]`/`[]`), locked dependency verification, helper syntax checks, drift
    scan, local artifact proof, release-version/public-doc/K8s tests, beta
    guard, release-version verifier, `validated 54 active changes`, main specs
    `30 passed, 0 failed`, Ruff, and whitespace.
  - The preflight now runs the live blocker snapshot helper directly before
    local artifact proof, so the one-command approval gate refreshes current
    public PR/workflow/release/repository/artifact visibility instead of only
    syntax-checking the snapshot helper.
  - 2026-06-14T05:51:19Z refresh after wiring the live snapshot into the
    preflight: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T05:51:19Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T05:51:19Z`), local artifact proof
    (`localArtifactProofAt=2026-06-14T05:51:22Z`),
    release-version/public-doc/K8s tests, beta guard, release-version verifier,
    `validated 54 active changes`, main specs `30 passed, 0 failed`, Ruff, and
    whitespace.
  - 2026-06-14T06:03:29Z refresh after aligning the PR draft to the latest
    snapshot: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:03:29Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:03:30Z`, `snapshotOptionalFailures=5`), local
    artifact proof (`localArtifactProofAt=2026-06-14T06:03:33Z`),
    release-version/public-doc/K8s tests (`106 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T07:37:08Z PR draft evidence guard refresh: public-release docs
    coverage now pins the paste-ready PR draft to the shared latest live
    snapshot timestamp and the hardened release title, public URL, and
    published timestamp evidence instead of accepting the older `06:36:14Z`
    snapshot as current.
  - 2026-06-14T07:37:08Z live-snapshot latest-label refresh: the goal brief's
    live public-state section now labels the shared latest full-preflight
    snapshot as the latest read-only refresh instead of leaving the older
    `07:11:40Z` standalone snapshot in that role, and public-release docs
    coverage rejects that stale latest label.
  - 2026-06-14T07:48:04Z live evidence refresh: public-release docs coverage now
    pins the paste-ready PR draft, staged prerelease notes, and goal latest
    snapshot label to the fresh full-preflight snapshot.
  - 2026-06-14T06:11:15Z refresh after naming live-snapshot optional failures:
    the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:11:15Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:11:16Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:11:19Z`),
    release-version/public-doc/K8s tests (`106 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T06:16:55Z refresh after adding the live-snapshot blocker
    summary: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:16:55Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:16:56Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:16:58Z`),
    release-version/public-doc/K8s tests (`106 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T06:25:32Z refresh after the publish-readiness success marker:
    the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:25:32Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:25:33Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:25:36Z`),
    release-version/public-doc/K8s tests (`106 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T06:36:13Z refresh after the PR-head evidence guard update: the
    same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:36:13Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:36:14Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:36:17Z`),
    release-version/public-doc/K8s tests (`107 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T06:44:35Z refresh after adding live-snapshot release-state
    blockers: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:44:35Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:44:36Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:44:39Z`),
    release-version/public-doc/K8s tests (`107 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T06:49:17Z refresh after adding live-snapshot repo-state
    blockers: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:49:17Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:49:17Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:49:20Z`),
    release-version/public-doc/K8s tests (`107 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T06:55:29Z refresh after exact release-asset proof hardening:
    the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T06:55:29Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T06:55:29Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T06:55:32Z`),
    release-version/public-doc/K8s tests (`107 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14 refresh after release-workflow dist hardening: `release.yml`
    now verifies the built `dist/` directory contains only the exact expected
    wheel and sdist filenames before the generic `dist/*` upload step can run.
    `uv run pytest -q tests/unit/test_public_release_docs.py` reported
    `78 passed`; `uv run pytest -q tests/unit/test_release_versions.py
    tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
    reported `108 passed`; `uv run pytest -q tests/unit/test_guard_beta_release.py`
    reported `10 passed`.
  - 2026-06-14T07:03:23Z full preflight refresh after release-workflow dist
    hardening: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T07:03:23Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T07:03:24Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:03:27Z`),
    release-version/public-doc/K8s tests (`108 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T07:15:03Z full preflight refresh after the live-snapshot
    evidence split: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T07:15:03Z`; it ran the live public snapshot
    (`snapshotAt=2026-06-14T07:15:04Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:15:07Z`),
    release-version/public-doc/K8s tests (`108 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T07:22:54Z full preflight refresh after the PR-head evidence
    refresh: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T07:22:54Z`; it ran the read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:22:55Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:22:57Z`),
    release-version/public-doc/K8s tests (`108 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T07:37:07Z full preflight refresh after release identity blocker
    hardening: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T07:37:07Z`; it ran the read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:37:08Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:37:10Z`),
    release-version/public-doc/K8s tests (`108 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T07:48:03Z full preflight refresh after the live evidence
    refresh: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T07:48:03Z`; it ran the read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:48:04Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:48:07Z`),
    release-version/public-doc/K8s tests (`108 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T07:56:47Z full preflight refresh after PyPI filename proof
    hardening: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T07:56:47Z`; it ran the read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T07:56:48Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T07:56:51Z`),
    release-version/public-doc/K8s tests (`108 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace. The PyPI JSON probe
    now requires exact `agent_lb-1.20.0b3-py3-none-any.whl` and
    `agent_lb-1.20.0b3.tar.gz` filenames once PyPI is visible.
  - 2026-06-14T08:07:12Z full preflight refresh after post-publish
    diagnostics hardening: the same one-command preflight passed again after
    printing `preflightAt=2026-06-14T08:07:12Z`; it ran the read-only PR/run
    checks (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T08:07:12Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T08:07:15Z`),
    release-version/public-doc/K8s tests (`108 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
  - 2026-06-14T08:38:25Z full preflight refresh after live/readiness evidence
    refresh: the same one-command preflight passed again after printing
    `preflightAt=2026-06-14T08:38:25Z`; it ran the read-only PR/run checks
    (`[]`/`[]`), live public snapshot
    (`snapshotAt=2026-06-14T08:38:26Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`),
    local artifact proof (`localArtifactProofAt=2026-06-14T08:38:29Z`),
    release-version/public-doc/K8s tests (`109 passed`), beta guard
    (`10 passed`), release-version verifier, `validated 54 active changes`,
    main specs `30 passed, 0 failed`, Ruff, and whitespace.
- [x] 4.10 Add and run repo-local post-publish artifact proof script coverage.
  - `./scripts/public-release-postpublish-proof.sh v1.20.0-beta.3` exited
    non-zero before publication on 2026-06-14T04:36:34Z after resolving
    `channel=beta` and `pypi_version=1.20.0b3`, then receiving PyPI JSON 404.
  - Coverage now also pins the post-publish proof contract for public
    repository metadata, GitHub release assets with exact wheel/sdist
    filenames, and replacement release body freshness, so the release cannot be
    called publicly complete while the GitHub about/homepage/topics, release
    assets, or prerelease notes still show stale state.
  - The proof contract also pins the GitHub release title, public release URL,
    and non-empty published timestamp, so the release cannot be called publicly
    complete while the release identity still points at stale or unpublished
    GitHub metadata.
  - 2026-06-14 PyPI artifact hardening: the live snapshot and post-publish
    proof now require the PyPI JSON release entry for `1.20.0b3` to include the
    exact expected wheel and sdist filenames, not only the package version.
  - Refresh on 2026-06-14T05:04:43Z: the post-publish proof script exited
    non-zero before publication at PyPI JSON 404, `uv run pytest -q
    tests/unit/test_public_release_docs.py` reported `75 passed`, the
    release/docs/Kubernetes slice reported `105 passed`, and strict validation
    for this OpenSpec change was valid.
  - 2026-06-14T05:35:44Z refresh: the script printed
    `postpublishProofAt=2026-06-14T05:35:44Z`, resolved `channel=beta` and
    `pypi_version=1.20.0b3`, then exited expected non-zero at PyPI JSON 404
    before the later GHCR/GitHub publication checks.
  - 2026-06-14T07:34:03Z refresh after release identity hardening: shell
    syntax passed, `uv run pytest -q tests/unit/test_public_release_docs.py`
    reported `78 passed`, the release/docs/Kubernetes slice reported
    `108 passed`, strict validation for this OpenSpec change was valid, main
    specs reported `30 passed, 0 failed`, Ruff passed, and whitespace was clean.
  - 2026-06-14T08:05:15Z post-publish diagnostics hardening: the
    post-publish proof now prints the expected PyPI version and exact
    wheel/sdist filenames before the PyPI JSON gate, so a published-but-misnamed
    artifact failure includes the expected public filenames in the operator
    output. A read-only `./scripts/public-release-postpublish-proof.sh
    v1.20.0-beta.3` run printed `postpublishProofAt=2026-06-14T08:06:34Z`,
    `expectedPypiVersion=1.20.0b3`,
    `expectedPypiWheelAsset=agent_lb-1.20.0b3-py3-none-any.whl`, and
    `expectedPypiSdistAsset=agent_lb-1.20.0b3.tar.gz`, then exited expected
    non-zero at the still-unpublished PyPI JSON 404 (`curl: (56)`). Shell
    syntax passed, `uv run pytest -q tests/unit/test_public_release_docs.py`
    reported `78 passed`, the
    release/docs/Kubernetes slice reported `108 passed`, strict validation for
    this OpenSpec change was valid, main specs reported `30 passed, 0 failed`,
    Ruff passed, and whitespace was clean.
  - 2026-06-14T08:20:38Z PyPI identity hardening: the post-publish proof and
    live snapshot now require PyPI JSON to match the expected public package
    summary and project URLs in addition to the selected version and exact
    wheel/sdist filenames. The read-only post-publish proof printed
    `expectedPypiSummary=ChatGPT and Claude account load balancer & proxy with
    usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints` plus
    the expected Homepage/Repository/Issues/Releases project URLs, then exited
    expected non-zero at the still-unpublished PyPI JSON 404 (`curl: (56)`);
    the live snapshot kept `snapshotOptionalFailures=5` and the same blocker
    names.
  - 2026-06-14T08:21:36Z verification after PyPI identity hardening: shell
    syntax passed; public release docs reported `78 passed`;
    release-version/public-doc/K8s tests reported `108 passed`;
    `require-beta-candidate-validation` strict validation passed; main specs
    reported `30 passed, 0 failed`; Ruff and whitespace were clean.
  - 2026-06-14T08:26:07Z OpenSpec contract alignment: the active
    release-management delta now normatively pins the post-publish PyPI
    summary/project-URL/exact wheel-sdist identity checks, live-snapshot PyPI
    identity visibility, publish-readiness local-main evidence, and PR-head
    `headRefOid`/Codex `head=` SHA matching contract. Public release docs
    reported `79 passed`; release-version/public-doc/K8s tests reported
    `109 passed`; `require-beta-candidate-validation` strict validation passed;
    main specs reported `30 passed, 0 failed`; Ruff and whitespace were clean.
  - `uv run pytest -q tests/unit/test_public_release_docs.py
    tests/unit/test_ci_workflow_required_checks.py` reported `74 passed`, and
    `uv run pytest -q tests/unit/test_public_release_docs.py
    tests/unit/test_k8s_version_policy.py` reported `73 passed`.
- [x] 4.11 Add and run repo-local publish-readiness guard coverage.
  - `./scripts/public-release-publish-readiness.sh v1.20.0-beta.3` exited
    non-zero before publication on 2026-06-14T04:52:56Z because the selected
    tag matched `HEAD` but the working tree still contained 166
    dirty/untracked paths.
  - 2026-06-14T05:27:43Z refresh: the same guard first verified
    `channel=beta` and `pypi_version=1.20.0b3`, then printed
    `dirty_count=167`, listed the current dirty/untracked paths, and exited
    non-zero before any publication command.
  - 2026-06-14T05:35:44Z refresh: the guard printed
    `publishReadinessAt=2026-06-14T05:35:44Z`, confirmed the approved tag still
    points at `HEAD`, verified `channel=beta` and `pypi_version=1.20.0b3`, then
    exited expected non-zero with `dirty_count=167`.
  - 2026-06-14T05:55:14Z refresh: the guard printed
    `publishReadinessAt=2026-06-14T05:55:14Z`, confirmed the approved tag still
    points at `HEAD`, verified `channel=beta` and `pypi_version=1.20.0b3`, then
    exited expected non-zero with `dirty_count=167`.
  - 2026-06-14T06:21:30Z refresh: the guard printed
    `publishReadinessAt=2026-06-14T06:21:30Z`, confirmed the approved tag still
    points at `HEAD`, verified `channel=beta` and `pypi_version=1.20.0b3`, then
    exited expected non-zero with `dirty_count=167`.
  - 2026-06-14T07:13:20Z refresh after live-snapshot evidence split: the guard
    printed `publishReadinessAt=2026-06-14T07:13:20Z`, confirmed the approved
    tag and `HEAD` are both `b00efd4fce34f42edb455a78b9cf34df8600e337`,
    verified `channel=beta` and `pypi_version=1.20.0b3`, then exited expected
    non-zero with `dirty_count=167`.
  - Coverage now pins a clean-path success marker:
    `publish readiness passed at ${PUBLISH_READINESS_AT}` prints only after tag,
    version/channel, clean-tree, open-PR, and main-run probes complete.
  - 2026-06-14 hardening: after local eligibility passes, the guard now captures
    live open-PR and main-run JSON, prints `open_pr_count` and
    `current_head_main_run_count`, fails closed if no returned `main` workflow
    run targets the current `HEAD`, and fails closed if any returned current-head
    run is not completed with a success, skipped, or neutral conclusion.
  - 2026-06-14T07:29:58Z refresh after current-head main-run guard hardening:
    the guard printed `publishReadinessAt=2026-06-14T07:29:58Z`, confirmed the
    approved tag and `HEAD` are both
    `b00efd4fce34f42edb455a78b9cf34df8600e337`, verified `channel=beta` and
    `pypi_version=1.20.0b3`, then exited expected non-zero with
    `dirty_count=167` before any live PR/run probe.
  - 2026-06-14T08:34:12Z refresh after local-main guard hardening: the guard
    printed `publishReadinessAt=2026-06-14T08:34:12Z`, confirmed
    `current_branch=main`, confirmed the approved tag, `HEAD`, and `main_sha`
    are all `b00efd4fce34f42edb455a78b9cf34df8600e337`, verified
    `channel=beta` and `pypi_version=1.20.0b3`, then exited expected non-zero
    with `dirty_count=167` before any live PR/run probe.
  - 2026-06-14T08:50:46Z read-only refresh after completion-boundary
    hardening: the guard printed `publishReadinessAt=2026-06-14T08:50:46Z`,
    confirmed `current_branch=main`, confirmed the approved tag, `HEAD`, and
    `main_sha` are all `b00efd4fce34f42edb455a78b9cf34df8600e337`, verified
    `channel=beta` and `pypi_version=1.20.0b3`, then exited expected non-zero
    with `dirty_count=167` before any live PR/run probe.
  - 2026-06-14T08:16:23Z verification after local-main guard hardening: shell
    syntax passed; public release docs reported `78 passed`;
    release-version/public-doc/K8s tests reported `108 passed`;
    `require-beta-candidate-validation` strict validation passed; main specs
    reported `30 passed, 0 failed`; Ruff and whitespace were clean.
  - 2026-06-14T08:26:07Z OpenSpec contract alignment: the active
    release-management delta now explicitly requires publish-readiness to fail
    when the checkout is not local `main`, when local `main` does not point at
    `HEAD`, and to print current-branch/local-main evidence before publication.
    Public release docs reported `79 passed`; release-version/public-doc/K8s
    tests reported `109 passed`; strict validation and formatting checks were
    clean.
- [x] 4.12 Add release helper syntax checks to the repo-local preflight.
  - The approval preflight now runs `bash -n` over
    `scripts/public-release-preflight.sh`,
    `scripts/public-release-live-snapshot.sh`,
    `scripts/public-release-publish-readiness.sh`,
    `scripts/public-release-postpublish-proof.sh`,
    `scripts/public-release-pr-head-proof.sh`,
    `scripts/public-release-local-artifact-proof.sh`,
    `scripts/public-release-runtime-proof.sh`, and
    `scripts/validate-active-openspec-changes.sh` before release tests and
    publication checks; the 2026-06-14T04:51:13Z preflight run passed this
    guard.
- [x] 4.13 Add and run a repo-local live release blocker snapshot.
  - `./scripts/public-release-live-snapshot.sh v1.20.0-beta.3` refreshes live
    read-only PR, workflow, release, repo metadata, PyPI, pip-index, GHCR image,
    GHCR alias, and Helm chart visibility while continuing through expected
    pre-publication misses; the 2026-06-14T05:18:11Z run printed
    `snapshotAt=2026-06-14T05:18:11Z` and completed with the known missing
    public artifact state.
  - 2026-06-14T05:55:14Z refresh: the helper printed
    `snapshotAt=2026-06-14T05:55:14Z`; open PRs and recent branch workflow runs
    still returned `[]`, the prerelease still has no assets, PyPI remains 404,
    and GHCR image/chart manifests remain denied/not visible.
  - 2026-06-14T05:59:27Z refresh after adding the optional-failure summary:
    the helper printed `snapshotAt=2026-06-14T05:59:27Z` and
    `snapshotOptionalFailures=5`; open PRs and recent branch workflow runs
    still returned `[]`, the prerelease still has no assets, PyPI remains 404,
    and the PyPI/pip-index/GHCR image/GHCR alias/Helm chart optional probes
    still account for the five read-only misses.
  - 2026-06-14T06:03:30Z refresh inside the full preflight: the helper printed
    `snapshotAt=2026-06-14T06:03:30Z` and `snapshotOptionalFailures=5`; open
    PRs and recent branch workflow runs still returned `[]`, the prerelease
    still has no assets, PyPI remains 404, and the same five read-only public
    artifact probes remain the current optional misses.
  - 2026-06-14T06:09:45Z refresh after naming optional failures: the helper
    printed `snapshotAt=2026-06-14T06:09:45Z`,
    `snapshotOptionalFailures=5`, and
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the
    prerelease still has no assets, PyPI remains 404, and the same five named
    read-only public artifact probes remain missing.
  - 2026-06-14T06:11:16Z refresh inside the full preflight: the helper printed
    `snapshotAt=2026-06-14T06:11:16Z`,
    `snapshotOptionalFailures=5`, and
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the
    prerelease still has no assets, PyPI remains 404, and the same five named
    read-only public artifact probes remain missing.
  - 2026-06-14T06:16:56Z refresh inside the full preflight after adding the
    blocker summary: the helper printed `snapshotAt=2026-06-14T06:16:56Z`,
    `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    and
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the
    prerelease still has no assets, PyPI remains 404, hosted repo metadata and
    release body are still stale, and the same five named read-only public
    artifact probes remain missing.
  - 2026-06-14T06:25:33Z refresh inside the full preflight after the
    publish-readiness success marker: the helper printed
    `snapshotAt=2026-06-14T06:25:33Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    and
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the
    prerelease still has no assets, PyPI remains 404, hosted repo metadata and
    release body are still stale, and the same five named read-only public
    artifact probes remain missing.
  - 2026-06-14T06:36:14Z refresh inside the full preflight after the PR-head
    evidence guard update: the helper printed
    `snapshotAt=2026-06-14T06:36:14Z`, `snapshotOptionalFailures=5`,
    `snapshotOptionalFailureNames=pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`,
    and
    `snapshotBlockingNames=release-assets,release-body,repo-description,repo-homepage,repo-topics,pypi-json,pip-index,ghcr-image-tag,ghcr-image-alias,ghcr-helm-chart`;
    open PRs and recent branch workflow runs still returned `[]`, the
    prerelease still has no assets, PyPI remains 404, hosted repo metadata and
    release body are still stale, and the same five named read-only public
    artifact probes remain missing.
  - 2026-06-14T06:43:07Z refresh after adding release-state blockers: the
    helper parsed `is_prerelease=true` and completed without adding
    `release-tag`, `release-prerelease`, or `release-draft` blockers; the same
    public artifact, repo metadata, stale release body, and missing release
    asset blockers remained.
  - 2026-06-14T06:44:36Z refresh inside the full preflight after the
    release-state blocker update: the helper parsed `is_prerelease=true` and
    again completed without adding `release-tag`, `release-prerelease`, or
    `release-draft` blockers; the same public artifact, repo metadata, stale
    release body, and missing release asset blockers remained.
  - 2026-06-14T06:49:17Z refresh inside the full preflight after adding
    repo-state blockers: the helper completed without adding
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; the same public artifact, repo metadata,
    stale release body, and missing release asset blockers remained.
  - 2026-06-14T06:55:29Z refresh inside the full preflight after exact
    release-asset proof hardening: the helper still completed without adding
    `release-tag`, `release-prerelease`, `release-draft`,
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; public artifacts, repo metadata, stale
    release body, and missing exact wheel/sdist release assets remained
    blockers.
  - 2026-06-14T07:03:24Z refresh inside the full preflight after exact
    release-workflow dist hardening: the helper still completed without adding
    `release-tag`, `release-prerelease`, `release-draft`,
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; public artifacts, repo metadata, stale
    release body, and missing exact wheel/sdist release assets remained
    blockers.
  - 2026-06-14T07:11:40Z read-only refresh: the helper again completed without
    adding `release-tag`, `release-prerelease`, `release-draft`,
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; open PRs/runs remained `[]`, and public
    artifacts, repo metadata, stale release body, and missing exact wheel/sdist
    release assets remained blockers. No GitHub mutations were made.
  - 2026-06-14T07:15:04Z refresh inside the full preflight: the helper again
    completed without adding `release-tag`, `release-prerelease`,
    `release-draft`, `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; open PRs/runs remained `[]`, and public
    artifacts, repo metadata, stale release body, and missing exact wheel/sdist
    release assets remained blockers. No GitHub mutations were made.
  - 2026-06-14T07:22:55Z refresh inside the full preflight: the helper again
    completed without adding `release-tag`, `release-prerelease`,
    `release-draft`, `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; open PRs/runs remained `[]`, and public
    artifacts, repo metadata, stale release body, and missing exact wheel/sdist
    release assets remained blockers. No GitHub mutations were made.
  - 2026-06-14 hardening: live snapshots now add named blockers for stale
    GitHub release title, stale public release URL, and missing published
    timestamp, matching the stricter post-publish proof identity contract.
  - 2026-06-14T07:35:29Z refresh after release identity blocker hardening:
    the helper completed without adding `release-tag`, `release-name`,
    `release-url`, `release-published`, `release-prerelease`,
    `release-draft`, `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; the remaining blockers were still release
    assets, release body, repo description/homepage/topics, PyPI, pip-index,
    GHCR image tag/alias, and GHCR Helm chart visibility.
  - 2026-06-14T07:37:08Z refresh inside the full preflight: the helper again
    completed without adding `release-tag`, `release-name`, `release-url`,
    `release-published`, `release-prerelease`, `release-draft`,
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; open PRs/runs remained `[]`, and the
    remaining blockers were still release assets, release body, repo
    description/homepage/topics, PyPI, pip-index, GHCR image tag/alias, and
    GHCR Helm chart visibility. No GitHub mutations were made.
  - 2026-06-14T07:48:04Z refresh inside the full preflight: the helper again
    completed without adding `release-tag`, `release-name`, `release-url`,
    `release-published`, `release-prerelease`, `release-draft`,
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; open PRs/runs remained `[]`, and the
    remaining blockers were still release assets, release body, repo
    description/homepage/topics, PyPI, pip-index, GHCR image tag/alias, and
    GHCR Helm chart visibility. No GitHub mutations were made.
  - 2026-06-14T07:56:48Z refresh inside the full preflight: the helper again
    completed without adding `release-tag`, `release-name`, `release-url`,
    `release-published`, `release-prerelease`, `release-draft`,
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; open PRs/runs remained `[]`, and the
    remaining blockers were still release assets, release body, repo
    description/homepage/topics, PyPI, pip-index, GHCR image tag/alias, and
    GHCR Helm chart visibility. The PyPI JSON probe now requires the exact
    expected wheel and sdist filenames once PyPI is visible. No GitHub mutations
    were made.
  - 2026-06-14T08:07:12Z refresh inside the full preflight: the helper again
    completed without adding `release-tag`, `release-name`, `release-url`,
    `release-published`, `release-prerelease`, `release-draft`,
    `repo-visibility`, `repo-private`, `repo-archived`, or
    `repo-default-branch` blockers; open PRs/runs remained `[]`, and the
    remaining blockers were still release assets, release body, repo
    description/homepage/topics, PyPI, pip-index, GHCR image tag/alias, and
    GHCR Helm chart visibility. No GitHub mutations were made.
  - 2026-06-14T08:34:06Z read-only refresh after OpenSpec contract alignment:
    open PRs/runs remained `[]`, the existing prerelease still had no assets
    and the older pricing/warmup body, hosted repo metadata remained stale/empty,
    PyPI still returned 404, pip index still found no distribution, and GHCR
    image/chart manifest checks were still denied/not visible. The helper kept
    `snapshotOptionalFailures=5` and the same blocker names. No GitHub mutations
    were made.
  - 2026-06-14T08:38:26Z refresh inside the full preflight: open PRs/runs
    remained `[]`, the existing prerelease still had no assets and the older
    pricing/warmup body, hosted repo metadata remained stale/empty, PyPI still
    returned 404, pip index still found no distribution, and GHCR image/chart
    manifest checks were still denied/not visible. The helper kept
    `snapshotOptionalFailures=5` and the same blocker names while checking the
    expected PyPI version, summary, project URLs, and exact wheel/sdist filenames.
    No GitHub mutations were made.
  - 2026-06-14T08:50:46Z standalone read-only refresh after completion-boundary
    hardening: open PRs/runs remained `[]`, the existing prerelease still had no
    assets and the older pricing/warmup body, hosted repo metadata remained
    stale/empty, PyPI still returned 404, pip index still found no distribution,
    and GHCR image/chart manifest checks were still denied/not visible. The
    helper kept `snapshotOptionalFailures=5` and the same blocker names. No
    GitHub mutations were made.
  - 2026-06-14T08:32:28Z snapshot-evidence alignment: the paste-ready PR draft,
    goal brief, and release-publication blocker text pinned the 08:27
    standalone snapshot while preserving the 08:07 full-preflight evidence as
    historical proof. `uv run pytest -q tests/unit/test_public_release_docs.py`
    reported `79 passed`; the release-version/public-doc/K8s slice reported
    `109 passed`; `require-beta-candidate-validation` strict validation passed;
    main specs reported `30 passed, 0 failed`; Ruff and whitespace were clean.
  - 2026-06-14T08:36:27Z live evidence refresh: the paste-ready PR draft, goal
    brief, release-publication blocker text, and publish-readiness guard
    evidence now pin the 08:34 live snapshot/readiness results. Public-release
    docs reported `79 passed`; the release-version/public-doc/K8s slice
    reported `109 passed`; `require-beta-candidate-validation` strict
    validation passed; main specs reported `30 passed, 0 failed`; Ruff and
    whitespace were clean.
  - 2026-06-14T08:42:42Z full-preflight evidence refresh: the paste-ready PR
    draft, goal brief, release-publication blocker text, and this OpenSpec task
    ledger now pin the 08:38 preflight/snapshot/artifact-proof results.
    Public-release docs reported `79 passed`; the release-version/public-doc/K8s
    slice reported `109 passed`; `require-beta-candidate-validation` strict
    validation passed; main specs reported `30 passed, 0 failed`; Ruff and
    whitespace were clean.
  - 2026-06-14T08:45:50Z continuation guardrail: public-release docs tests now
    pin the final handoff remaining-risk section so the release cannot be
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
    release-version/public-doc/K8s slice reported `110 passed`;
    `require-beta-candidate-validation` strict validation passed; Ruff and
    whitespace were clean. No GitHub mutations were made.
  - 2026-06-14T08:54:03Z verification after refreshing the 08:50 live/readiness
    evidence: public-release docs tests reported `80 passed`; the
    release-version/public-doc/K8s slice reported `110 passed`;
    `require-beta-candidate-validation` strict validation passed; Ruff and
    whitespace were clean. No GitHub mutations were made.
- [x] 4.14 Harden and rerun public screenshot capture.
  - `cd frontend && bun run screenshots` first exposed a stale-preview hazard:
    Playwright reused an unrelated app already listening on `localhost:4173`.
    The screenshot harness now runs the repo-built Agent LB preview on its own
    `127.0.0.1:4174` base URL, refuses existing-server reuse, and navigates
    scenes through Playwright's configured `baseURL`. The rerun on
    2026-06-14T03:17:46Z reported `7 passed` and regenerated the public
    dashboard, accounts, settings, login, and dark-mode README screenshots.
- [x] 4.15 Add and run repo-local PR-head proof helper coverage.
  - `scripts/public-release-pr-head-proof.sh` now provides the read-only
    current-head gate command for the remaining PR-only proof step. It fails
    closed after printing `prHeadProofAt` unless the PR is open, non-draft,
    based on `main`, canonical-headed, merge-clean, labeled `🤖 codex: ok`
    without `🤖 codex: needs work`, has passing required checks including
    `CI Required`, prints `pr_head_sha` and `pr_head_short`, and the
    current-head Codex classifier reports the same `head=${pr_head_short}`,
    successful checks, and a clean review.
  - Dry-run absence proof on 2026-06-14T05:40:38Z:
    `./scripts/public-release-pr-head-proof.sh 0` printed
    `prHeadProofAt=2026-06-14T05:40:38Z` and then failed with
    `exit_status=1` because `gh pr view` reported `no pull requests found`.
  - SHA-identity dry-run absence proof on 2026-06-14T08:17:46Z:
    `./scripts/public-release-pr-head-proof.sh 0` printed
    `prHeadProofAt=2026-06-14T08:17:46Z` and then failed with
    `exit_status=1` because `gh pr view` still reported
    `no pull requests found`; successful proofs now require the Codex
    classifier's `head=` fragment to match the PR `headRefOid` short SHA.
  - `./scripts/public-release-preflight.sh v1.20.0-beta.3` passed on
    2026-06-14T04:51:13Z with the PR-head proof helper included in the release
    helper syntax check.
  - The public PR template and contributor merge-gate docs now require
    approval-gated release/package/publication PRs to name
    `./scripts/public-release-pr-head-proof.sh <pr-number>` alongside the live
    snapshot, publish-readiness, and post-publish proof commands.
  - Focused verification on 2026-06-14T05:42:14Z: `uv run pytest -q
    tests/unit/test_public_release_docs.py` -> `76 passed`;
    `uv run pytest -q tests/unit/test_release_versions.py
    tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
    -> `106 passed`; strict validation for `require-beta-candidate-validation`
    and `create-pytest-required-check-placeholders` -> valid; main specs
    -> `30 passed, 0 failed`; Ruff format/check for
    `tests/unit/test_public_release_docs.py` and `git diff --check` passed.
  - SHA-identity verification on 2026-06-14T08:18:57Z: shell syntax passed;
    public release docs reported `78 passed`;
    release-version/public-doc/K8s tests reported `108 passed`;
    `require-beta-candidate-validation` strict validation passed; main specs
    reported `30 passed, 0 failed`; Ruff and whitespace were clean.
- [x] 4.16 Add and run repo-local local artifact proof coverage.
  - `scripts/public-release-local-artifact-proof.sh` now verifies the selected
    release tag's local wheel and sdist before publication by deriving the
    PyPI-normalized version, requiring matching `dist/` artifacts, comparing
    the sdist README hash to the repository README, rejecting dev-only
    top-level sdist paths, checking wheel metadata, and running `twine check`.
  - The public release approval preflight includes the helper in both the
    syntax-check set and the read-only verification sequence.
  - `./scripts/public-release-local-artifact-proof.sh v1.20.0-beta.3` passed on
    2026-06-14T05:35:44Z after printing
    `localArtifactProofAt=2026-06-14T05:35:44Z`, and the full
    `./scripts/public-release-preflight.sh v1.20.0-beta.3` passed on
    2026-06-14T04:51:13Z with the helper included.
  - 2026-06-14T06:11:19Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T06:11:19Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, and `twine check`.
  - 2026-06-14T06:16:58Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T06:16:58Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T06:25:36Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T06:25:36Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T06:44:39Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T06:44:39Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T06:49:20Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T06:49:20Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T06:55:32Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T06:55:32Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T07:03:27Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T07:03:27Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T07:15:07Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T07:15:07Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T07:22:57Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T07:22:57Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T07:37:10Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T07:37:10Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T07:48:07Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T07:48:07Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T07:56:51Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T07:56:51Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T08:07:15Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T08:07:15Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
  - 2026-06-14T08:38:29Z refresh inside the full preflight: the helper printed
    `localArtifactProofAt=2026-06-14T08:38:29Z`, verified the selected local
    wheel/sdist, sdist README freshness, dev-only top-level path exclusion,
    wheel metadata, README image tag references, and `twine check`.
- [x] 4.17 Add and run repo-local runtime proof coverage.
  - `scripts/public-release-runtime-proof.sh` now verifies, after approved
    restart/reinstall, that the local daemon is healthy and
    `/api/runtime/version` reports the approved version, no pending update, and
    the fork release URL.
  - `./scripts/public-release-runtime-proof.sh v1.20.0-beta.3` exited
    expected non-zero before approved restart/reinstall on
    2026-06-14T05:35:44Z after printing
    `runtimeProofAt=2026-06-14T05:35:44Z` (`rc=1`): release metadata parsed,
    health passed, and the runtime assertion returned `false` because the
    healthy daemon still serves the old upstream release URL.
  - 2026-06-14T05:55:14Z refresh: the helper printed
    `runtimeProofAt=2026-06-14T05:55:14Z` and exited expected non-zero (`rc=1`);
    release metadata parsed, health passed, and the runtime assertion still
    returned `false` because the healthy daemon has not been
    restarted/reinstalled from the candidate.
  - 2026-06-14T06:21:57Z refresh: the helper printed
    `runtimeProofAt=2026-06-14T06:21:57Z` and exited expected non-zero (`rc=1`);
    release metadata parsed, health passed, and the runtime assertion still
    returned `false` because the healthy daemon has not been
    restarted/reinstalled from the candidate.
  - 2026-06-14T07:19:03Z refresh: the helper printed
    `runtimeProofAt=2026-06-14T07:19:03Z` and exited expected non-zero (`rc=1`);
    release metadata parsed, health passed, and the runtime assertion still
    returned `false` because the healthy daemon has not been
    restarted/reinstalled from the candidate.
