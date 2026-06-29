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

checks_out="$(mktemp)"
checks_err="$(mktemp)"
codex_out="$(mktemp)"
codex_err="$(mktemp)"
trap 'rm -f "${checks_out}" "${checks_err}" "${codex_out}" "${codex_err}"' EXIT

echo "+ gh pr view ${PR_NUMBER} --repo ${REPO}"
pr_json="$(
  gh pr view "${PR_NUMBER}" \
    --repo "${REPO}" \
    --json number,state,isDraft,baseRefName,headRefName,headRefOid,headRepositoryOwner,mergeable,mergeStateStatus,reviewDecision,labels,url
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
    and any(.labels[]?.name; . == "🤖 codex: ok")
    and all(.labels[]?.name; . != "🤖 codex: needs work")
  ' >/dev/null

echo "+ gh pr checks ${PR_NUMBER} --repo ${REPO} --required"
set +e
gh pr checks "${PR_NUMBER}" \
  --repo "${REPO}" \
  --required \
  --json bucket,completedAt,link,name,startedAt,state,workflow \
  >"${checks_out}" \
  2>"${checks_err}"
checks_status="$?"
set -e

cat "${checks_out}"
if [ -s "${checks_err}" ]; then
  cat "${checks_err}" >&2
fi

checks_json="$(cat "${checks_out}")"
printf '%s\n' "${checks_json}" | jq -e '
  length > 0
  and any(.[]; .name == "CI Required" and (.bucket == "pass" or .state == "SUCCESS"))
  and all(.[]; .bucket == "pass" or .state == "SUCCESS")
' >/dev/null

if [ "${checks_status}" -ne 0 ]; then
  echo "required PR checks are not all passing; gh pr checks exited ${checks_status}" >&2
  exit 1
fi

echo "+ python3 .github/scripts/sync_codex_ok_labels.py --repo ${REPO} --pr ${PR_NUMBER}"
set +e
python3 .github/scripts/sync_codex_ok_labels.py \
  --repo "${REPO}" \
  --pr "${PR_NUMBER}" \
  --no-trigger-missing-codex \
  --no-approve-workflow-runs \
  >"${codex_out}" \
  2>"${codex_err}"
codex_status="$?"
set -e

cat "${codex_out}"
if [ -s "${codex_err}" ]; then
  cat "${codex_err}" >&2
fi

if [ "${codex_status}" -ne 0 ]; then
  echo "Codex review classifier failed; refusing PR-head proof" >&2
  exit 1
fi

codex_output="$(cat "${codex_out}")"

require_codex_fragment() {
  local fragment="$1"
  if [[ "${codex_output}" != *"${fragment}"* ]]; then
    echo "Codex review classifier output did not prove: ${fragment}" >&2
    exit 1
  fi
}

require_codex_fragment "dry-run ${REPO}#${PR_NUMBER}:"
require_codex_fragment "head=${pr_head_short}"
require_codex_fragment "checks=success"
require_codex_fragment "merge=CLEAN"
require_codex_fragment "review=clean"
require_codex_fragment "ok=True->True/keep"
require_codex_fragment "needs_work=False->False/keep"

echo "PR head proof passed for ${REPO}#${PR_NUMBER} at ${PR_HEAD_PROOF_AT}"
