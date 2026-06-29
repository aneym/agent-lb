#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 <approved-release-tag> [release-channel] [base-url]" >&2
  exit 2
fi

RELEASE_TAG="$1"
RELEASE_CHANNEL="${2:-beta}"
BASE_URL="${3:-http://127.0.0.1:2455}"
EXPECTED_RELEASE_URL="https://github.com/aneym/agent-lb/releases/latest"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT}"

RUNTIME_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "runtimeProofAt=${RUNTIME_PROOF_AT}"

release_metadata="$(uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}" --require-channel "${RELEASE_CHANNEL}")"
printf '%s\n' "${release_metadata}"

expected_version="$(printf '%s\n' "${release_metadata}" | awk -F= '$1 == "version" {print $2}')"

if [ -z "${expected_version}" ]; then
  echo "could not parse version metadata from scripts.verify_release_version" >&2
  exit 1
fi

echo "+ curl -fsS ${BASE_URL}/health"
curl -fsS "${BASE_URL}/health" | jq -e '.status == "ok"'

echo "+ curl -fsS ${BASE_URL}/api/runtime/version"
runtime_payload="$(curl -fsS "${BASE_URL}/api/runtime/version")"
printf '%s\n' "${runtime_payload}" | jq -e \
  --arg expected_version "${expected_version}" \
  --arg expected_release_url "${EXPECTED_RELEASE_URL}" \
  '
    .currentVersion == $expected_version
    and .updateAvailable == false
    and .releaseUrl == $expected_release_url
    and (.checkedAt | type == "string" and length > 0)
  '

echo "runtime release proof passed at ${RUNTIME_PROOF_AT}"
