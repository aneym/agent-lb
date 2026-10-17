#!/usr/bin/env bash




























set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
target="${1:-default}"

CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION:-2.1.282}"
CODEX_VERSION="${CODEX_VERSION:-0.157.0}"
NODE_VERSION="${NODE_VERSION:-22.23.3}"
UV_VERSION="${UV_VERSION:-0.12.19}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14.5}"
AGENT_LB_REPO="${AGENT_LB_REPO:-/Volumes/StudioExt/repos/agent-lb}"
AGENT_LB_REF="${AGENT_LB_REF:-origin/main}"
AGENT_RAILS_URL="${AGENT_RAILS_URL:-https://github.com/shelf-group/agent-rails.git}"
AGENT_RAILS_REF="${AGENT_RAILS_REF:-main}"
AGENT_RAILS_DEPTH="${AGENT_RAILS_DEPTH:-300}"
AGENT_RAILS_BASE="${AGENT_RAILS_BASE:-$AGENT_RAILS_REF}"

agents_tag="${AGENTS_IMAGE:-of-work/agents:cc${CLAUDE_CODE_VERSION}-codex${CODEX_VERSION}-pd1.3.0}"
ctx=""
trap 'if [ -n "$ctx" ]; then rm -r -f -- "$ctx"; fi' EXIT

build_agents() {
  docker build \
    --build-arg "CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION}" \
    --build-arg "CODEX_VERSION=${CODEX_VERSION}" \
    --build-arg "NODE_VERSION=${NODE_VERSION}" \
    -t "$agents_tag" -t of-work/agents:latest \
    "$here/images/agents"
  docker run --rm "$agents_tag" sh -c 'cat /opt/of-agents/VERSIONS; claude --version; codex --version'
}

bundle_pi_durable() {
  local container cache
  cache="${PI_DURABLE_CACHE:-$HOME/eval-scratch/pi-durable-1.3.0.tgz}"
  mkdir -p "$(dirname "$cache")"
  ctx="$(mktemp -d "${TMPDIR:-/tmp}/of-pi-bundle.XXXXXX")"
  container="$(docker create "$agents_tag")"
  docker cp "$container:/opt/pi-durable" "$ctx/pi-durable"
  docker rm "$container" >/dev/null
  python3 - "$ctx" <<'PYBUNDLE'
import sys
import tarfile
from pathlib import Path
root = Path(sys.argv[1])
with tarfile.open(root / "bundle.tgz", "w:gz", format=tarfile.GNU_FORMAT) as archive:
    archive.add(root / "pi-durable", arcname="pi-durable")
PYBUNDLE
  mv "$ctx/bundle.tgz" "$cache"
  rm -r -f -- "$ctx"; ctx=""
  if [ -n "${PI_DURABLE_HOST:-}" ]; then
    ssh "$PI_DURABLE_HOST" 'mkdir -p ~/eval-scratch'
    rsync -a "$cache" "$PI_DURABLE_HOST:eval-scratch/pi-durable-1.3.0.tgz"
  fi
  echo "pi-durable bundle: $cache"
}

new_context() {
  local name="$1"
  ctx="$(mktemp -d "${TMPDIR:-/tmp}/of-${name}-ctx.XXXXXX")"
  cp "$here/images/repo-base/Dockerfile" "$here/images/of-checkout" "$here/images/${name}-base/of-sync" "$ctx/"
}


docker_build_base() {
  local name="$1" ctx="$2" sha="$3"
  shift 3
  test "$(git -C "$ctx/src.git" rev-parse refs/of/base)" = "$sha"
  docker build \
    --build-arg "AGENTS_IMAGE=${agents_tag}" \
    --build-arg "UV_IMAGE=ghcr.io/astral-sh/uv:${UV_VERSION}" \
    --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
    --build-arg "BASE_SHA=${sha}" \
    --label "of.base-sha=${sha}" \
    "$@" \
    -t "of-work/${name}-base:${sha:0:12}" -t "of-work/${name}-base:latest" \
    "$ctx"
  echo "built of-work/${name}-base:${sha:0:12} (${sha})"
}

build_agent_lb() {
  local sha
  sha="$(git -C "$AGENT_LB_REPO" rev-parse --verify "${AGENT_LB_REF}^{commit}")"
  new_context agent-lb
  git init -q --bare "$ctx/src.git"
  git -C "$AGENT_LB_REPO" push -q "$ctx/src.git" "${sha}:refs/of/base"
  docker_build_base agent-lb "$ctx" "$sha"
  rm -r -f -- "$ctx"; ctx=""
}

build_agent_rails() {
  local sha
  : "${GITHUB_TOKEN:?agent-rails is private; export GITHUB_TOKEN}"
  new_context agent-rails


  git -c credential.helper= \
    -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f' \
    clone -q --bare --no-tags --depth "$AGENT_RAILS_DEPTH" --single-branch \
    --branch "$AGENT_RAILS_REF" "$AGENT_RAILS_URL" "$ctx/src.git"
  git -C "$ctx/src.git" remote remove origin
  sha="$(git -C "$ctx/src.git" rev-parse --verify "${AGENT_RAILS_BASE}^{commit}")"
  git -C "$ctx/src.git" update-ref refs/of/base "$sha"
  docker_build_base agent-rails "$ctx" "$sha" --build-arg REPO_PYTHONPATH=/app
  rm -r -f -- "$ctx"; ctx=""
}

case "$target" in
  agents) build_agents; bundle_pi_durable ;;
  pi-durable) bundle_pi_durable ;;
  agent-lb) build_agent_lb ;;
  agent-rails) build_agent_rails ;;
  default) build_agents; bundle_pi_durable; build_agent_lb ;;
  all) build_agents; bundle_pi_durable; build_agent_lb; build_agent_rails ;;
  *) echo "usage: $0 [agents|pi-durable|agent-lb|agent-rails|all]" >&2; exit 2 ;;
esac
