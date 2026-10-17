# Plan 002: The upstream release, beta and Kubernetes distribution machinery is gone

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- .github deploy/helm scripts Makefile Dockerfile.distroless docker-compose.prod.yml .all-contributorsrc GOAL.md HANDOFF.md SUMMARY.md docs/plans README.md GETTING-STARTED.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" facts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (plan 003 edits the same `Makefile` and `scripts/local_ci.py` after this one)
- **Category**: tech-debt
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

agent-lb is a fork of codex-lb. It inherited a public release pipeline: beta
releases through release-please, PyPI/GHCR publishing, a distroless image, a
Helm chart with kind smoke tests, all-contributors attribution and the
June 2026 "public release readiness" handoff docs. `PRODUCT.md` now says the
users are "One operator (Alex) and occasionally teammates, running a local
LLM-proxy load balancer (`127.0.0.1:2455`)". The service runs under launchd,
not Kubernetes or Docker. The last GitHub release is `v1.20.0-beta.3` from
2026-06-11; since then the only workflow runs are `Stale issues and PRs` and
`PR labeler`. The owner asked on 2026-10-17 to remove legacy code. About
11,000 lines go.

## Current state

Deadness evidence at `63b62f18`:

- `gh run list -L 10` shows only `Stale issues and PRs` and `PR labeler` runs (October).
- `gh release list -L 3`: newest `v1.20.0-beta.3`, 2026-06-11.
- No file under `~/Library/LaunchAgents` mentions `docker`, `helm` or `kubectl` for agent-lb.
- `scripts/run-agent-lb.sh` (the launchd entry) does not use Docker.
- The last local CI receipt (`~/agent-lb-ci/receipts/*/*/receipt.json`) is from 2026-09-23. `scripts/local_ci.py` is not used to land work; agent-lb lands by fast-forward push to `main`.

Files to delete:

| Group | Paths | Lines |
|-------|-------|------:|
| Release workflows and config | `.github/workflows/prepare-beta-release.yml`, `.github/workflows/publish-beta-release.yml`, `.github/workflows/release-please.yml`, `.github/workflows/release.yml`, `.github/release-please-config.json`, `.github/release-please-manifest.json` | ~1,300 |
| Contributors | `.all-contributorsrc`, `.github/scripts/check_all_contributors.py` | ~420 |
| Dead label sync | `.github/scripts/sync_codex_ok_labels.py` (its workflow `.github/workflows/codex-review-labels.yml` no longer exists) | small |
| Release scripts | `scripts/guard_beta_release.py`, `scripts/prepare_beta_release.py`, `scripts/cleanup_superseded_beta_prs.py`, `scripts/release_versions.py`, `scripts/release_metadata.py`, `scripts/verify_release_version.py`, `scripts/public-release-*.sh` (8 files) | ~2,000 |
| Kubernetes | `deploy/helm/` (37 files), `scripts/helm-kind-smoke.sh` | ~3,700 |
| Distroless image | `Dockerfile.distroless`, `scripts/distroless-entrypoint.py`, `docker-compose.prod.yml` | ~100 |
| Stale handoff docs | `GOAL.md`, `HANDOFF.md`, `SUMMARY.md` (tracked even though `.gitignore` line 60 ignores it), `docs/plans/2026-05-28-codex-lb-canonical-e2e-goal.md`, `docs/plans/2026-06-09-claude-lb-durable-final-state-goal.md` | ~3,700 |
| Their unit tests | `tests/unit/test_guard_beta_release.py`, `test_release_versions.py`, `test_public_release_docs.py`, `test_cleanup_superseded_beta_prs.py`, `test_check_all_contributors.py`, `test_sync_codex_ok_labels.py`, `test_helm_external_secrets.py`, `test_k8s_version_policy.py`, `test_ci_workflow_required_checks.py` (it asserts `.github/workflows/ci.yml` and `windows-startup.yml`, which do not exist) | — |

Keep: `deploy/launchd/` (2 plists), `Dockerfile`, `docker-compose.yml`,
`scripts/docker-entrypoint.sh` (local dev compose), `.github/workflows/stale.yml`,
`.github/workflows/pr-labeler.yml`, `.github/labeler.yml`, `.github/dependabot.yml`,
`.github/CODEOWNERS`, `.github/SECURITY.md`, `LICENSE`, `CHANGELOG.md`
(AGENTS.md rule 5: never edit `CHANGELOG.md`).

Wiring that must change:

```make
# Makefile:187-190
CI_TARGETS := contributors beta-release-guard frontend-lint frontend-typecheck frontend-test frontend-build \
	lint typecheck test-unit test-integration-core test-integration-bridge test-e2e \
	test-postgres migration-check migration-check-postgres package docker helm-check helm-smoke-kind \
	menubar-test menubar-build
```

Also the Makefile targets `contributors`, `beta-release-guard`, `helm-deps`,
`helm-lint`, `helm-template`, `helm-kubeconform`, `helm-check`,
`helm-smoke-kind`, their `.PHONY` entries, the `help` text lines that name
them, and the `CI_CLUSTER` variable if nothing else uses it.

```python
# scripts/local_ci.py:40-61 BASELINE_TARGETS must equal `make -s ci-targets` exactly
BASELINE_TARGETS = {"contributors", "beta-release-guard", ..., "helm-check", "helm-smoke-kind", ...}
# scripts/local_ci.py:24 IMAGE_TARGETS = {"docker", "helm-smoke-kind"}
# scripts/local_ci.py REQUIRED_TOOLS (around lines 25-39) lists helm, kind, kubectl, kubeconform
```

Remove `contributors`, `beta-release-guard`, `helm-check`, `helm-smoke-kind`
from both sets, `helm-smoke-kind` from `IMAGE_TARGETS`, and `helm`, `kind`,
`kubectl`, `kubeconform` from the required tools if only the removed targets
used them (`grep -n "kind\|helm\|kubectl\|kubeconform" scripts/local_ci.py`).
Keep `docker` (it builds the kept `Dockerfile`).

Docs to trim (delete whole sections, do not rewrite the rest):
- `README.md`: the "Published beta artifacts" section (starts line 138), the "## Kubernetes" section (starts about line 766), and the "## Contributors" section with its all-contributors table (about line 803 to the end of the table).
- `.github/CONTRIBUTING.md` and `.github/PULL_REQUEST_TEMPLATE.md`: lines that name `tests/unit/test_public_release_docs.py`, release-please, beta releases or Helm.
- `docs/local-ci.md`: remove Helm/kind/kubeconform from the tools paragraph; delete the sentence "Release, publication, attribution labels and stale-issue automation are not local CI and are not removed."

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/cull-release -b cull/release origin/main` | exit 0 |
| Env | `sh scripts/worktree-setup.sh` | exit 0 |
| Target list | `make -s ci-targets` | lists no `contributors`, `beta-release-guard`, `helm-*` |
| Gate parity | `python3 -c "import subprocess,sys; sys.path.insert(0,'scripts'); import local_ci; t=set(subprocess.check_output(['make','-s','ci-targets'],text=True).split()); assert t==local_ci.BASELINE_TARGETS, t^local_ci.BASELINE_TARGETS; print('ok')"` | prints `ok` |
| Lint | `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .` | exit 0 |
| Doctor | `python3 scripts/local_ci.py doctor` | does not name helm, kind, kubectl or kubeconform |
| Dead refs | `git grep -nE "release_versions|guard_beta_release|prepare_beta_release|public-release-|helm-kind-smoke|all-contributors|deploy/helm|Dockerfile.distroless" -- . ':!CHANGELOG.md' ':!openspec'` | no output |

## Scope

**In scope**: every path in the delete table; `Makefile`; `scripts/local_ci.py`;
`README.md`, `.github/CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
`docs/local-ci.md` (section deletions only); `.gitignore` line 60 (`SUMMARY.md`) may stay.

**Out of scope**:
- `CHANGELOG.md` (repo rule 5).
- `openspec/` (plan 008).
- `app/` settings for multi-replica or leader election: the blue/green `lb-restart` runs two instances, so these may be live. Do not touch.
- `stale.yml`, `pr-labeler.yml`, `dependabot.yml`: live automation.

## Git workflow

- Branch `cull/release`; one commit, e.g. `Remove the upstream release, beta and Helm distribution machinery`.
- Body: one `check:` line with the Gate parity and Dead refs results, and a `Seat:` trailer.
- Land: `git push origin HEAD:main` (fast-forward only).

## Steps

### Step 1: Re-check that nothing publishes

Run `gh run list -L 30 --json workflowName,createdAt` and `gh release list -L 1`.

**Verify**: no run of `Release`, `Prepare beta release`, `Publish beta release` or `release-please` after 2026-06-30, and the newest release is `v1.20.0-beta.3`. Otherwise STOP.

### Step 2: Check the owner has not asked for public releases since

Run `alex-said "agent-lb public release"` and `alex-said "agent-lb helm"` if the command exists.

**Verify**: no statement dated after 2026-09-01 asks to keep publishing agent-lb releases or the Helm chart. If one does, STOP.

### Step 3: Delete the files

`git rm -r` every path in the delete table.

**Verify**: `git status --short | grep -c "^D"` is at least 60.

### Step 4: Rewire Make and local CI

Edit `Makefile` and `scripts/local_ci.py` as described in Current state.

**Verify**: the Target list, Gate parity and Doctor commands give their expected results.

### Step 5: Trim the docs

Delete the named sections only.

**Verify**: the Dead refs command prints nothing.

### Step 6: Lint and land

**Verify**: the Lint command exits 0; `git push origin HEAD:main` succeeds. No restart is needed (no `app/` change).

## Test plan

No new tests. The deleted unit tests only guarded deleted files. The
replacement check is the Gate parity command plus `make lint`.

## Done criteria

- [ ] `test ! -e deploy/helm && test ! -e Dockerfile.distroless && test ! -e GOAL.md && test ! -e HANDOFF.md` exits 0
- [ ] Gate parity prints `ok`
- [ ] Dead refs prints nothing
- [ ] `uv run --no-sync ruff check .` exits 0
- [ ] `plans/README.md` row 002 updated

## STOP conditions

- Step 1 or 2 shows releases are still wanted.
- Any file under `~/Library/LaunchAgents`, `~/.agent-lb/bin`, `/Volumes/StudioExt/repos/factory` or `/Volumes/StudioExt/repos/agent-rails` references a deleted path (`rg -l "guard_beta_release|release_versions|deploy/helm|public-release-" <those paths>`).
- `make -s ci-targets` and `BASELINE_TARGETS` cannot be made equal without removing a tier this plan does not name.

## Maintenance notes

- If the owner later wants agent-lb published, restore from git (`git show 63b62f18:<path>`) rather than rebuilding.
- Reviewer: confirm the README still explains the local launchd install path end to end.

LOC removed (estimate): ~11,000 (including the deleted unit tests).
