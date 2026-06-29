#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <approved-release-tag> [release-channel]" >&2
  exit 2
fi

RELEASE_TAG="$1"
RELEASE_CHANNEL="${2:-beta}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

SNAPSHOT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SNAPSHOT_OPTIONAL_FAILURES=0
SNAPSHOT_OPTIONAL_FAILURE_NAMES=()
SNAPSHOT_BLOCKING_NAMES=()
CAPTURED_OPTIONAL_OUTPUT=""
CAPTURED_OPTIONAL_RC=0
echo "snapshotAt=${SNAPSHOT_AT}"

add_blocker() {
  SNAPSHOT_BLOCKING_NAMES+=("$1")
}

record_optional_failure() {
  local check_name="$1"
  shift
  local rc="$1"
  shift

  SNAPSHOT_OPTIONAL_FAILURES=$((SNAPSHOT_OPTIONAL_FAILURES + 1))
  SNAPSHOT_OPTIONAL_FAILURE_NAMES+=("${check_name}")
  add_blocker "${check_name}"
  echo "optional check ${check_name} exited ${rc}: $*" >&2
}

run_optional() {
  local check_name="$1"
  shift
  echo "+ $*"
  "$@"
  local rc=$?
  if [ "${rc}" -ne 0 ]; then
    record_optional_failure "${check_name}" "${rc}" "$@"
  fi
}

run_shell_optional() {
  local check_name="$1"
  shift
  echo "+ $*"
  bash -o pipefail -c "$*"
  local rc=$?
  if [ "${rc}" -ne 0 ]; then
    record_optional_failure "${check_name}" "${rc}" "$@"
  fi
}

run_capture_optional() {
  local check_name="$1"
  shift
  echo "+ $*"
  CAPTURED_OPTIONAL_OUTPUT="$("$@" 2>&1)"
  CAPTURED_OPTIONAL_RC=$?
  printf '%s\n' "${CAPTURED_OPTIONAL_OUTPUT}"
  if [ "${CAPTURED_OPTIONAL_RC}" -ne 0 ]; then
    record_optional_failure "${check_name}" "${CAPTURED_OPTIONAL_RC}" "$@"
  fi
}

EXPECTED_DESCRIPTION="ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints"
EXPECTED_HOMEPAGE="https://github.com/aneym/agent-lb"
EXPECTED_TOPICS_JSON='["python","oauth","sqlalchemy","dashboard","load-balancer","openai","anthropic","claude","rate-limit","api-proxy","codex","fastapi","usage-tracking","chatgpt","opencode","openclaw"]'
EXPECTED_PYPI_PROJECT_URLS_JSON='{"Homepage":"https://github.com/aneym/agent-lb","Repository":"https://github.com/aneym/agent-lb","Issues":"https://github.com/aneym/agent-lb/issues","Releases":"https://github.com/aneym/agent-lb/releases"}'

echo "+ uv run python -m scripts.verify_release_version --tag ${RELEASE_TAG} --require-channel ${RELEASE_CHANNEL}"
release_metadata="$(uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}" --require-channel "${RELEASE_CHANNEL}")" || exit 1
printf '%s\n' "${release_metadata}"

release_version="$(printf '%s\n' "${release_metadata}" | awk -F= '$1 == "version" {print $2}')"
pypi_version="$(printf '%s\n' "${release_metadata}" | awk -F= '$1 == "pypi_version" {print $2}')"
is_prerelease="$(printf '%s\n' "${release_metadata}" | awk -F= '$1 == "is_prerelease" {print $2}')"

if [ -z "${release_version}" ] || [ -z "${pypi_version}" ] || [ -z "${is_prerelease}" ]; then
  echo "could not parse release metadata from scripts.verify_release_version" >&2
  exit 1
fi

wheel_asset="agent_lb-${pypi_version}-py3-none-any.whl"
sdist_asset="agent_lb-${pypi_version}.tar.gz"
release_url="https://github.com/aneym/agent-lb/releases/tag/${RELEASE_TAG}"
release_name="Release ${RELEASE_TAG}"

IMAGE_ALIAS="${RELEASE_CHANNEL}"
if [ "${RELEASE_CHANNEL}" = "stable" ]; then
  IMAGE_ALIAS="latest"
fi

run_optional open-pr-list gh pr list --repo aneym/agent-lb --state open --json number,title,headRefName,baseRefName,state,url,updatedAt
run_optional branch-run-list gh run list --repo aneym/agent-lb --branch main --limit 10 --json databaseId,status,conclusion,workflowName,headBranch,headSha,createdAt,url
run_optional release-list gh release list --repo aneym/agent-lb --limit 10 --json tagName,name,isPrerelease,isDraft,publishedAt,isLatest,isImmutable,createdAt
run_capture_optional release-view gh release view "${RELEASE_TAG}" --repo aneym/agent-lb --json tagName,name,isPrerelease,isDraft,publishedAt,url,assets,body
release_view_json="${CAPTURED_OPTIONAL_OUTPUT}"
release_view_rc="${CAPTURED_OPTIONAL_RC}"
run_capture_optional repo-metadata gh repo view aneym/agent-lb --json description,homepageUrl,repositoryTopics,isArchived,isPrivate,visibility,url,defaultBranchRef
repo_metadata_json="${CAPTURED_OPTIONAL_OUTPUT}"
repo_metadata_rc="${CAPTURED_OPTIONAL_RC}"

if [ "${release_view_rc}" -eq 0 ]; then
  if ! printf '%s\n' "${release_view_json}" | jq -e --arg expected_tag "${RELEASE_TAG}" '.tagName == $expected_tag' >/dev/null; then
    add_blocker release-tag
  fi
  if ! printf '%s\n' "${release_view_json}" | jq -e --arg expected_name "${release_name}" '.name == $expected_name' >/dev/null; then
    add_blocker release-name
  fi
  if ! printf '%s\n' "${release_view_json}" | jq -e --arg expected_url "${release_url}" '.url == $expected_url' >/dev/null; then
    add_blocker release-url
  fi
  if ! printf '%s\n' "${release_view_json}" | jq -e '(.publishedAt // "") != ""' >/dev/null; then
    add_blocker release-published
  fi
  if ! printf '%s\n' "${release_view_json}" | jq -e --argjson expected_prerelease "${is_prerelease}" '.isPrerelease == $expected_prerelease' >/dev/null; then
    add_blocker release-prerelease
  fi
  if ! printf '%s\n' "${release_view_json}" | jq -e '.isDraft == false' >/dev/null; then
    add_blocker release-draft
  fi
  if ! printf '%s\n' "${release_view_json}" | jq -e --arg wheel_asset "${wheel_asset}" --arg sdist_asset "${sdist_asset}" '
      ([.assets[]?.name] | index($wheel_asset) != null and index($sdist_asset) != null)
    ' >/dev/null; then
    add_blocker release-assets
  fi
  if ! printf '%s\n' "${release_view_json}" | jq -e '
      (.body // "" | contains("Beta prerelease for Agent LB public-release readiness."))
      and (((.body // "") | contains("duplicate column name")) | not)
      and (((.body // "") | contains("tests/integration/test_migrations.py currently fails")) | not)
    ' >/dev/null; then
    add_blocker release-body
  fi
fi

if [ "${repo_metadata_rc}" -eq 0 ]; then
  if ! printf '%s\n' "${repo_metadata_json}" | jq -e --arg expected_description "${EXPECTED_DESCRIPTION}" '.description == $expected_description' >/dev/null; then
    add_blocker repo-description
  fi
  if ! printf '%s\n' "${repo_metadata_json}" | jq -e --arg expected_homepage "${EXPECTED_HOMEPAGE}" '.homepageUrl == $expected_homepage' >/dev/null; then
    add_blocker repo-homepage
  fi
  if ! printf '%s\n' "${repo_metadata_json}" | jq -e --argjson expected_topics "${EXPECTED_TOPICS_JSON}" '
      def topic_name:
        if type == "string" then .
        elif has("name") then .name
        elif has("topic") then .topic.name
        else empty
        end;
      ([.repositoryTopics[]? | topic_name] | sort) == ($expected_topics | sort)
    ' >/dev/null; then
    add_blocker repo-topics
  fi
  if ! printf '%s\n' "${repo_metadata_json}" | jq -e '.visibility == "PUBLIC"' >/dev/null; then
    add_blocker repo-visibility
  fi
  if ! printf '%s\n' "${repo_metadata_json}" | jq -e '.isPrivate == false' >/dev/null; then
    add_blocker repo-private
  fi
  if ! printf '%s\n' "${repo_metadata_json}" | jq -e '.isArchived == false' >/dev/null; then
    add_blocker repo-archived
  fi
  if ! printf '%s\n' "${repo_metadata_json}" | jq -e '.defaultBranchRef.name == "main"' >/dev/null; then
    add_blocker repo-default-branch
  fi
fi

run_shell_optional pypi-json "curl -fsS https://pypi.org/pypi/agent-lb/json | jq -e --arg pypi_version '${pypi_version}' --arg wheel_asset '${wheel_asset}' --arg sdist_asset '${sdist_asset}' --arg expected_description '${EXPECTED_DESCRIPTION}' --argjson expected_project_urls '${EXPECTED_PYPI_PROJECT_URLS_JSON}' '.info.version == \$pypi_version and .info.summary == \$expected_description and ((.info.project_urls // {}) == \$expected_project_urls) and ([.releases[\$pypi_version][]?.filename] | index(\$wheel_asset) != null and index(\$sdist_asset) != null)'"
run_shell_optional pip-index "python3 -m pip index versions agent-lb | rg --fixed-strings '${pypi_version}'"
run_optional ghcr-image-tag docker manifest inspect "ghcr.io/aneym/agent-lb:${release_version}"
run_optional ghcr-image-alias docker manifest inspect "ghcr.io/aneym/agent-lb:${IMAGE_ALIAS}"
run_optional ghcr-helm-chart docker manifest inspect "ghcr.io/aneym/charts/agent-lb:${release_version}"

echo "snapshotOptionalFailures=${SNAPSHOT_OPTIONAL_FAILURES}"
if [ "${SNAPSHOT_OPTIONAL_FAILURES}" -eq 0 ]; then
  echo "snapshotOptionalFailureNames=[]"
else
  optional_failure_names="$(IFS=,; printf '%s' "${SNAPSHOT_OPTIONAL_FAILURE_NAMES[*]}")"
  echo "snapshotOptionalFailureNames=${optional_failure_names}"
fi
if [ "${#SNAPSHOT_BLOCKING_NAMES[@]}" -eq 0 ]; then
  echo "snapshotBlockingNames=[]"
else
  blocking_names="$(IFS=,; printf '%s' "${SNAPSHOT_BLOCKING_NAMES[*]}")"
  echo "snapshotBlockingNames=${blocking_names}"
fi
echo "snapshot complete at ${SNAPSHOT_AT}"
