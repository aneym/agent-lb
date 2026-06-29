#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

failures=0

scan_fixed() {
  local label="$1"
  local pattern="$2"
  shift 2

  echo "+ rg --fixed-strings -n '${pattern}' $*"
  local output
  local rc
  set +e
  output="$(rg --fixed-strings -n "${pattern}" "$@" 2>/dev/null)"
  rc=$?
  set -e

  if [ "${rc}" -eq 0 ]; then
    echo "public release drift found: ${label}" >&2
    printf '%s\n' "${output}" >&2
    failures=1
  elif [ "${rc}" -eq 1 ]; then
    echo "ok: ${label}"
  else
    echo "drift scan failed for ${label}" >&2
    failures=1
  fi
}

scan_regex() {
  local label="$1"
  local pattern="$2"
  shift 2

  echo "+ rg -n '${pattern}' $*"
  local output
  local rc
  set +e
  output="$(rg -n "${pattern}" "$@" 2>/dev/null)"
  rc=$?
  set -e

  if [ "${rc}" -eq 0 ]; then
    echo "public release drift found: ${label}" >&2
    printf '%s\n' "${output}" >&2
    failures=1
  elif [ "${rc}" -eq 1 ]; then
    echo "ok: ${label}"
  else
    echo "drift scan failed for ${label}" >&2
    failures=1
  fi
}

public_install_surfaces=(
  README.md
  GETTING-STARTED.md
  deploy/helm/agent-lb/README.md
  pyproject.toml
  .github/workflows
  .github/PULL_REQUEST_TEMPLATE.md
  .github/CONTRIBUTING.md
  .github/SECURITY.md
  .github/ISSUE_TEMPLATE
  .github/DISCUSSION_TEMPLATE
  openspec/specs
)

runtime_release_surfaces=(
  app
  clients
  frontend
  README.md
  GETTING-STARTED.md
  .github
  deploy
  pyproject.toml
  .agents/skills/agent-lb-account-operator
  .agents/skills/get-started
  .agents/skills/skill-rules.json
  .agents/conventions
)

screenshot_surfaces=(
  README.md
  GETTING-STARTED.md
  AGENTS.md
  .github
  docs
  deploy
  openspec/specs
  .agents/skills/agent-lb-account-operator
  .agents/skills/get-started
)

public_name_surfaces=(
  README.md
  GETTING-STARTED.md
  .github
  deploy
  pyproject.toml
  .agents/skills/agent-lb-account-operator
  .agents/skills/get-started
  .agents/skills/skill-rules.json
  openspec/specs
)

scan_fixed \
  "unpublished latest Docker tag in public install surfaces" \
  "ghcr.io/aneym/agent-lb:latest" \
  "${public_install_surfaces[@]}"

scan_fixed \
  "old upstream release URL in shipping/runtime surfaces" \
  "https://github.com/Soju06/agent-lb/releases/latest" \
  "${runtime_release_surfaces[@]}"

scan_regex \
  "deleted public screenshot artifact references" \
  "apis-assigned-accounts|codex-session-retag-(docker|wsl)-(apply|dry-run)" \
  "${screenshot_surfaces[@]}"

scan_regex \
  "retired fork or stale hosted description in public surfaces" \
  'swap-lb|feat/anthropic-provider|upstream `aneym/agent-lb`|Codex/ChatGPT multiple account load balancer' \
  "${public_name_surfaces[@]}"

if [ "${failures}" -ne 0 ]; then
  exit 1
fi

echo "public release drift scan passed"
