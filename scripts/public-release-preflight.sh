#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <approved-release-tag> [release-channel]" >&2
  exit 2
fi

RELEASE_TAG="$1"
RELEASE_CHANNEL="${2:-beta}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT}"

PREFLIGHT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "preflightAt=${PREFLIGHT_AT}"

echo "+ git status --short"
git status --short

echo "+ git diff --stat"
git diff --stat

echo "+ git rev-parse HEAD"
git rev-parse HEAD

echo "+ git rev-parse ${RELEASE_TAG}^{}"
git rev-parse "${RELEASE_TAG}^{}"

echo "+ gh pr list --repo aneym/agent-lb --state open"
gh pr list --repo aneym/agent-lb --state open --json number,title,headRefName,baseRefName,state,url,updatedAt

echo "+ gh run list --repo aneym/agent-lb --branch main --limit 10"
gh run list --repo aneym/agent-lb --branch main --limit 10 --json databaseId,status,conclusion,workflowName,headBranch,headSha,createdAt,url

echo "+ uv lock --locked"
uv lock --locked

echo "+ bash -n scripts/public-release-preflight.sh scripts/public-release-drift-scan.sh scripts/public-release-live-snapshot.sh scripts/public-release-publish-readiness.sh scripts/public-release-postpublish-proof.sh scripts/public-release-pr-head-proof.sh scripts/public-release-local-artifact-proof.sh scripts/public-release-runtime-proof.sh scripts/validate-active-openspec-changes.sh"
bash -n scripts/public-release-preflight.sh scripts/public-release-drift-scan.sh scripts/public-release-live-snapshot.sh scripts/public-release-publish-readiness.sh scripts/public-release-postpublish-proof.sh scripts/public-release-pr-head-proof.sh scripts/public-release-local-artifact-proof.sh scripts/public-release-runtime-proof.sh scripts/validate-active-openspec-changes.sh

echo "+ ./scripts/public-release-drift-scan.sh"
./scripts/public-release-drift-scan.sh

echo "+ ./scripts/public-release-live-snapshot.sh ${RELEASE_TAG} ${RELEASE_CHANNEL}"
./scripts/public-release-live-snapshot.sh "${RELEASE_TAG}" "${RELEASE_CHANNEL}"

echo "+ ./scripts/public-release-local-artifact-proof.sh ${RELEASE_TAG} ${RELEASE_CHANNEL}"
./scripts/public-release-local-artifact-proof.sh "${RELEASE_TAG}" "${RELEASE_CHANNEL}"

echo "+ uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py"
uv run pytest -q tests/unit/test_release_versions.py tests/unit/test_public_release_docs.py tests/unit/test_k8s_version_policy.py

echo "+ uv run pytest -q tests/unit/test_guard_beta_release.py"
uv run pytest -q tests/unit/test_guard_beta_release.py

echo "+ uv run python -m scripts.verify_release_version --tag ${RELEASE_TAG} --require-channel ${RELEASE_CHANNEL}"
uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}" --require-channel "${RELEASE_CHANNEL}"

echo "+ ./scripts/validate-active-openspec-changes.sh"
./scripts/validate-active-openspec-changes.sh

echo "+ npx --yes @fission-ai/openspec@latest validate --specs"
npx --yes @fission-ai/openspec@latest validate --specs

echo "+ uvx ruff format --check tests/unit/test_public_release_docs.py"
uvx ruff format --check tests/unit/test_public_release_docs.py

echo "+ uvx ruff check tests/unit/test_public_release_docs.py"
uvx ruff check tests/unit/test_public_release_docs.py

echo "+ git diff --check"
git diff --check

echo "preflight complete at ${PREFLIGHT_AT}"
