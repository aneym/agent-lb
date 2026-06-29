## 1. CI workflow

- [x] 1.1 Remove the job-level backend path filter from the pytest matrix job so
      GitHub creates all required pytest matrix check contexts.
- [x] 1.2 Add a cheap successful placeholder step for non-backend changes.
- [x] 1.3 Keep checkout, dependency setup, and real pytest execution gated on
      backend changes.

## 2. Verification

- [x] 2.1 Add regression tests for the workflow shape.
- [x] 2.2 Run focused unit tests for the workflow regression coverage.
  - 2026-06-14: `uv run pytest -q tests/unit/test_ci_workflow_required_checks.py` -> 4 passed.
- [x] 2.3 Validate the OpenSpec change strictly.
  - 2026-06-14: `npx --yes @fission-ai/openspec@latest validate create-pytest-required-check-placeholders --strict` -> valid.
- [ ] 2.4 Confirm GitHub CI and Codex review on the PR head.
  - Pending until the release-ready tree is committed and a PR exists.
    Read-only refresh on 2026-06-14T07:20:58Z:
    `gh pr list --repo aneym/agent-lb --state open` returned `[]`, and
    `gh run list --repo aneym/agent-lb --branch main --limit 10` returned
    `[]`; `git status --porcelain | wc -l` reported `167` dirty/untracked
    paths. Once a PR exists, run
    `./scripts/public-release-pr-head-proof.sh <pr-number>`.
- [x] 2.5 Add and run repo-local PR-head proof helper coverage.
  - `scripts/public-release-pr-head-proof.sh` now provides the read-only
    current-head gate command for the remaining PR-only proof step. It fails
    closed after printing `prHeadProofAt` unless the PR is open, non-draft,
    based on `main`, canonical-headed, merge-clean, labeled `🤖 codex: ok`
    without `🤖 codex: needs work`, has passing required checks including
    `CI Required`, and the current-head Codex classifier reports successful
    checks and a clean review.
  - Dry-run absence proof on 2026-06-14T05:40:38Z:
    `./scripts/public-release-pr-head-proof.sh 0` printed
    `prHeadProofAt=2026-06-14T05:40:38Z` and then failed with
    `exit_status=1` because `gh pr view` reported `no pull requests found`.
  - Focused verification on 2026-06-14T05:42:14Z: `uv run pytest -q
    tests/unit/test_public_release_docs.py` -> `76 passed`;
    `uv run pytest -q tests/unit/test_release_versions.py
    tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py`
    -> `106 passed`; strict validation for `require-beta-candidate-validation`
    and `create-pytest-required-check-placeholders` -> valid; main specs
    -> `30 passed, 0 failed`; Ruff format/check for
    `tests/unit/test_public_release_docs.py` and `git diff --check` passed.
