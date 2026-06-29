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

LOCAL_ARTIFACT_PROOF_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "localArtifactProofAt=${LOCAL_ARTIFACT_PROOF_AT}"

release_metadata="$(uv run python -m scripts.verify_release_version --tag "${RELEASE_TAG}" --require-channel "${RELEASE_CHANNEL}")"
printf '%s\n' "${release_metadata}"

pypi_version="$(printf '%s\n' "${release_metadata}" | awk -F= '$1 == "pypi_version" {print $2}')"
release_version="$(printf '%s\n' "${release_metadata}" | awk -F= '$1 == "version" {print $2}')"

if [ -z "${pypi_version}" ] || [ -z "${release_version}" ]; then
  echo "could not parse version metadata from scripts.verify_release_version" >&2
  exit 1
fi

wheel="dist/agent_lb-${pypi_version}-py3-none-any.whl"
sdist="dist/agent_lb-${pypi_version}.tar.gz"
sdist_root="agent_lb-${pypi_version}"
metadata_path="agent_lb-${pypi_version}.dist-info/METADATA"

for artifact in "${wheel}" "${sdist}"; do
  if [ ! -f "${artifact}" ]; then
    echo "missing local release artifact: ${artifact}" >&2
    exit 1
  fi
done

echo "+ tar -tzf ${sdist} README/pyproject entries"
tar -tzf "${sdist}" | rg "^${sdist_root}/(README\.md|pyproject\.toml)$"

echo "+ compare README.md hash with ${sdist} README.md"
root_readme_hash="$(shasum -a 256 README.md | awk '{print $1}')"
sdist_readme_hash="$(tar -xOf "${sdist}" "${sdist_root}/README.md" | shasum -a 256 | awk '{print $1}')"
if [ "${root_readme_hash}" != "${sdist_readme_hash}" ]; then
  echo "sdist README.md does not match repository README.md" >&2
  echo "repo=${root_readme_hash}" >&2
  echo "sdist=${sdist_readme_hash}" >&2
  exit 1
fi

echo "+ scan ${sdist} for dev-only top-level paths"
forbidden_entries="$(tar -tzf "${sdist}" | rg "^${sdist_root}/(\.agents|\.github|clients|frontend|tests|docs|openspec|\.build|__pycache__|\.venv|node_modules)(/|$)" || true)"
if [ -n "${forbidden_entries}" ]; then
  echo "sdist contains dev-only top-level paths:" >&2
  printf '%s\n' "${forbidden_entries}" >&2
  exit 1
fi

echo "+ unzip -p ${wheel} ${metadata_path}"
metadata="$(unzip -p "${wheel}" "${metadata_path}")"
printf '%s\n' "${metadata}" | rg --fixed-strings "Name: agent-lb"
printf '%s\n' "${metadata}" | rg --fixed-strings "Version: ${pypi_version}"
printf '%s\n' "${metadata}" | rg --fixed-strings "Summary: ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints"
printf '%s\n' "${metadata}" | rg --fixed-strings "Maintainer: Alex Neyman"
printf '%s\n' "${metadata}" | rg --fixed-strings "Classifier: Development Status :: 4 - Beta"
printf '%s\n' "${metadata}" | rg --fixed-strings "ghcr.io/aneym/agent-lb:${release_version}"

echo "+ uvx --from twine==6.2.0 twine check ${wheel} ${sdist}"
uvx --from twine==6.2.0 twine check "${wheel}" "${sdist}"

echo "local release artifact proof passed at ${LOCAL_ARTIFACT_PROOF_AT}"
