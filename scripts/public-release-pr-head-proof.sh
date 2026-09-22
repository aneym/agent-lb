#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <pr-number> [repo]" >&2
  exit 2
fi

PR_NUMBER="$1"
REPO="${2:-aneym/agent-lb}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL_OWNER="${REPO%%/*}"

if ! [[ "${PR_NUMBER}" =~ ^[0-9]+$ ]]; then
  echo "PR number must be numeric" >&2
  exit 2
fi

if [ -z "${CANONICAL_OWNER}" ] || [ "${CANONICAL_OWNER}" = "${REPO}" ]; then
  echo "repo must be formatted as owner/name" >&2
  exit 2
fi

cd "${ROOT}"

PR_HEAD_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "prHeadProofAt=${PR_HEAD_PROOF_AT}"

echo "+ gh pr view ${PR_NUMBER} --repo ${REPO}"
pr_json="$(
  gh pr view "${PR_NUMBER}" \
    --repo "${REPO}" \
    --json number,state,isDraft,baseRefName,headRefName,headRefOid,headRepositoryOwner,mergeable,mergeStateStatus,reviewDecision,url
)"
printf '%s\n' "${pr_json}"

pr_head_sha="$(printf '%s\n' "${pr_json}" | jq -r '.headRefOid // ""')"
if [ -z "${pr_head_sha}" ]; then
  echo "pull request headRefOid was missing" >&2
  exit 1
fi
pr_head_short="${pr_head_sha:0:12}"
echo "pr_head_sha=${pr_head_sha}"
echo "pr_head_short=${pr_head_short}"

printf '%s\n' "${pr_json}" | jq -e \
  --argjson pr_number "${PR_NUMBER}" \
  --arg owner "${CANONICAL_OWNER}" \
  '
    .number == $pr_number
    and .state == "OPEN"
    and .isDraft == false
    and .baseRefName == "main"
    and .headRepositoryOwner.login == $owner
    and .mergeable == "MERGEABLE"
    and .mergeStateStatus == "CLEAN"
    and .reviewDecision != "CHANGES_REQUESTED"
  ' >/dev/null

echo "+ python3 scripts/local_ci.py status ${pr_head_sha}"
python3 scripts/local_ci.py status "${pr_head_sha}"
echo "+ python3 scripts/local_ci.py show ${pr_head_sha}"
python3 scripts/local_ci.py show "${pr_head_sha}"

echo "PR head proof passed for ${REPO}#${PR_NUMBER} at ${PR_HEAD_PROOF_AT}"
