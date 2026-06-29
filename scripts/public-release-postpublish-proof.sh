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

POSTPUBLISH_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "postpublishProofAt=${POSTPUBLISH_PROOF_AT}"

release_metadata="$(uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}" --require-channel "${RELEASE_CHANNEL}")"
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

EXPECTED_DESCRIPTION="ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints"
EXPECTED_HOMEPAGE="https://github.com/aneym/agent-lb"
EXPECTED_TOPICS_JSON='["python","oauth","sqlalchemy","dashboard","load-balancer","openai","anthropic","claude","rate-limit","api-proxy","codex","fastapi","usage-tracking","chatgpt","opencode","openclaw"]'
EXPECTED_PYPI_PROJECT_URLS_JSON='{"Homepage":"https://github.com/aneym/agent-lb","Repository":"https://github.com/aneym/agent-lb","Issues":"https://github.com/aneym/agent-lb/issues","Releases":"https://github.com/aneym/agent-lb/releases"}'

echo "expectedPypiVersion=${pypi_version}"
echo "expectedPypiWheelAsset=${wheel_asset}"
echo "expectedPypiSdistAsset=${sdist_asset}"
echo "expectedPypiSummary=${EXPECTED_DESCRIPTION}"
echo "expectedPypiProjectUrls=${EXPECTED_PYPI_PROJECT_URLS_JSON}"
echo "+ curl -fsS https://pypi.org/pypi/agent-lb/json"
pypi_json="$(curl -fsS https://pypi.org/pypi/agent-lb/json)"
printf '%s\n' "${pypi_json}" \
  | jq -e \
      --arg pypi_version "${pypi_version}" \
      --arg wheel_asset "${wheel_asset}" \
      --arg sdist_asset "${sdist_asset}" \
      --arg expected_description "${EXPECTED_DESCRIPTION}" \
      --argjson expected_project_urls "${EXPECTED_PYPI_PROJECT_URLS_JSON}" \
      '
        .info.version == $pypi_version
        and .info.summary == $expected_description
        and ((.info.project_urls // {}) == $expected_project_urls)
        and ([.releases[$pypi_version][]?.filename] | index($wheel_asset) != null and index($sdist_asset) != null)
      '

echo "+ python3 -m pip index versions agent-lb"
python3 -m pip index versions agent-lb | rg --fixed-strings "${pypi_version}"

echo "+ docker manifest inspect ghcr.io/aneym/agent-lb:${release_version}"
docker manifest inspect "ghcr.io/aneym/agent-lb:${release_version}"

echo "+ docker manifest inspect ghcr.io/aneym/agent-lb:${IMAGE_ALIAS}"
docker manifest inspect "ghcr.io/aneym/agent-lb:${IMAGE_ALIAS}"

echo "+ docker manifest inspect ghcr.io/aneym/charts/agent-lb:${release_version}"
docker manifest inspect "ghcr.io/aneym/charts/agent-lb:${release_version}"

echo "+ gh repo view aneym/agent-lb"
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

echo "+ gh release view ${RELEASE_TAG} --repo aneym/agent-lb"
gh release view "${RELEASE_TAG}" --repo aneym/agent-lb --json tagName,name,assets,isPrerelease,isDraft,publishedAt,url,body \
  | jq -e \
      --arg expected_tag "${RELEASE_TAG}" \
      --arg expected_name "${release_name}" \
      --arg expected_url "${release_url}" \
      --argjson expected_prerelease "${is_prerelease}" \
      --arg wheel_asset "${wheel_asset}" \
      --arg sdist_asset "${sdist_asset}" \
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

echo "post-publish proof complete at ${POSTPUBLISH_PROOF_AT}"
