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

PUBLISH_READINESS_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "publishReadinessAt=${PUBLISH_READINESS_AT}"

head_sha="$(git rev-parse HEAD)"
tag_sha="$(git rev-parse "${RELEASE_TAG}^{}")"
current_branch="$(git branch --show-current)"
main_sha="$(git rev-parse main)"

echo "head_sha=${head_sha}"
echo "tag_sha=${tag_sha}"
echo "current_branch=${current_branch}"
echo "main_sha=${main_sha}"

if [ "${current_branch}" != "main" ]; then
  echo "publish readiness must run from local main; current branch is ${current_branch:-detached}" >&2
  exit 1
fi

if [ "${head_sha}" != "${main_sha}" ]; then
  echo "local main does not point at HEAD" >&2
  exit 1
fi

if [ "${head_sha}" != "${tag_sha}" ]; then
  echo "release tag ${RELEASE_TAG} does not point at HEAD" >&2
  exit 1
fi

echo "+ uv run python -m scripts.verify_release_version --tag ${RELEASE_TAG} --require-channel ${RELEASE_CHANNEL}"
uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}" --require-channel "${RELEASE_CHANNEL}"

dirty_paths="$(git status --porcelain)"
if [ -n "${dirty_paths}" ]; then
  dirty_count="$(printf '%s\n' "${dirty_paths}" | wc -l | tr -d ' ')"
  echo "dirty_count=${dirty_count}" >&2
  echo "working tree is dirty; commit or intentionally exclude these paths before publishing:" >&2
  printf '%s\n' "${dirty_paths}" >&2
  exit 1
fi

echo "+ gh pr list --repo aneym/agent-lb --state open"
open_prs_json="$(gh pr list --repo aneym/agent-lb --state open --json number,title,headRefName,baseRefName,state,url,updatedAt)"
printf '%s\n' "${open_prs_json}"
open_pr_count="$(printf '%s\n' "${open_prs_json}" | jq 'length')"
echo "open_pr_count=${open_pr_count}"

echo "+ gh run list --repo aneym/agent-lb --branch main --limit 10"
main_runs_json="$(gh run list --repo aneym/agent-lb --branch main --limit 10 --json databaseId,status,conclusion,workflowName,headBranch,headSha,createdAt,url)"
printf '%s\n' "${main_runs_json}"
current_head_main_run_count="$(
  printf '%s\n' "${main_runs_json}" \
    | jq --arg head_sha "${head_sha}" '[.[] | select(.headSha == $head_sha)] | length'
)"
echo "current_head_main_run_count=${current_head_main_run_count}"
if [ "${current_head_main_run_count}" -eq 0 ]; then
  echo "no current-head main workflow runs found for ${head_sha}" >&2
  exit 1
fi
non_green_current_head_runs="$(
  printf '%s\n' "${main_runs_json}" \
    | jq -r --arg head_sha "${head_sha}" '
        .[]
        | select(.headSha == $head_sha)
        | select((.status != "completed") or ((.conclusion // "") as $conclusion | ($conclusion != "success" and $conclusion != "skipped" and $conclusion != "neutral")))
        | "run \(.databaseId) \(.workflowName): status=\(.status) conclusion=\(.conclusion // "null") url=\(.url)"
      '
)"
if [ -n "${non_green_current_head_runs}" ]; then
  echo "current-head main workflow runs are not publish-ready:" >&2
  printf '%s\n' "${non_green_current_head_runs}" >&2
  exit 1
fi

echo "publish readiness passed at ${PUBLISH_READINESS_AT}"
