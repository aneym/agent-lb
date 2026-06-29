#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
cd "${ROOT}"

CHANGES_DIR="openspec/changes"
if [ ! -d "${CHANGES_DIR}" ]; then
  echo "active OpenSpec changes directory not found: ${CHANGES_DIR}" >&2
  exit 1
fi

total=0
while IFS= read -r change_dir; do
  change="$(basename "${change_dir}")"
  total=$((total + 1))
  npx --yes @fission-ai/openspec@latest validate "${change}" --strict
done < <(find "${CHANGES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name archive | sort)

echo "validated ${total} active changes"
