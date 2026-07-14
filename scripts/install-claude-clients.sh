#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(realpath "$REPO_DIR")"
BIN_DIR="${AGENT_LB_CLIENT_BIN_DIR:-$HOME/.local/bin}"
POLICY_DIR="${AGENT_LB_POLICY_DIR:-$HOME/.agents/policy}"
CLAUDE_BIN="${AGENT_LB_CLAUDE_BIN:-claude}"
MODE="install"

case "${1:-}" in
  --print) MODE="print" ;;
  --uninstall) MODE="uninstall" ;;
  "") ;;
  *) echo "usage: $0 [--print | --uninstall]" >&2; exit 2 ;;
esac

source_for() {
  case "$1" in
    cc) printf '%s\n' "$REPO_DIR/clients/cc" ;;
    ccdex) printf '%s\n' "$REPO_DIR/clients/ccdex" ;;
    ccdex-worker-mcp) printf '%s\n' "$REPO_DIR/clients/ccdex-worker-mcp" ;;
    *) return 1 ;;
  esac
}

for name in cc ccdex ccdex-worker-mcp; do
  source=$(source_for "$name")
  if [[ ! -x "$source" ]]; then
    echo "error: $source is missing or not executable" >&2
    exit 1
  fi
done
POLICY_SOURCE="$REPO_DIR/config/coding-agents"
if [[ ! -f "$POLICY_SOURCE/ROUTING.md" || ! -x "$POLICY_SOURCE/verify-routing" ]]; then
  echo "error: canonical coding-agent policy is incomplete at $POLICY_SOURCE" >&2
  exit 1
fi

print_plan() {
  for name in cc ccdex ccdex-worker-mcp; do
    echo "link $BIN_DIR/$name -> $(source_for "$name")"
  done
  echo "link $POLICY_DIR/coding-agents -> $POLICY_SOURCE"
  echo "mcp ccdex-worker -> $BIN_DIR/ccdex-worker-mcp (user scope)"
}

if [[ "$MODE" == "print" ]]; then
  print_plan
  exit 0
fi

if [[ "$MODE" == "uninstall" ]]; then
  for name in cc ccdex ccdex-worker-mcp; do
    target="$BIN_DIR/$name"
    source=$(source_for "$name")
    if [[ -L "$target" && "$(readlink "$target")" == "$source" ]]; then
      rm "$target"
      echo "removed $target"
    fi
  done
  policy_target="$POLICY_DIR/coding-agents"
  if [[ -L "$policy_target" && "$(readlink "$policy_target")" == "$POLICY_SOURCE" ]]; then
    rm "$policy_target"
    echo "removed $policy_target"
  fi
  "$CLAUDE_BIN" mcp remove --scope user ccdex-worker >/dev/null 2>&1 || true
  echo "removed user MCP registration ccdex-worker"
  exit 0
fi

mkdir -p "$BIN_DIR"
for name in cc ccdex ccdex-worker-mcp; do
  target="$BIN_DIR/$name"
  source=$(source_for "$name")
  if [[ -e "$target" && ! -L "$target" ]]; then
    backup="$target.pre-agent-lb"
    if [[ ! -e "$backup" ]]; then
      mv "$target" "$backup"
      echo "preserved existing $target at $backup"
    else
      echo "error: refusing to replace $target; backup already exists at $backup" >&2
      exit 1
    fi
  fi
  ln -sfn "$source" "$target"
  echo "linked $target -> $source"
done

mkdir -p "$POLICY_DIR"
policy_target="$POLICY_DIR/coding-agents"
if [[ -e "$policy_target" && ! -L "$policy_target" ]]; then
  policy_backup="$policy_target.pre-agent-lb"
  if [[ ! -e "$policy_backup" ]]; then
    mv "$policy_target" "$policy_backup"
    echo "preserved existing $policy_target at $policy_backup"
  else
    echo "error: refusing to replace $policy_target; backup already exists at $policy_backup" >&2
    exit 1
  fi
fi
ln -sfn "$POLICY_SOURCE" "$policy_target"
echo "linked $policy_target -> $POLICY_SOURCE"

"$CLAUDE_BIN" mcp remove --scope user ccdex-worker >/dev/null 2>&1 || true
"$CLAUDE_BIN" mcp add --scope user ccdex-worker -- "$BIN_DIR/ccdex-worker-mcp" >/dev/null
echo "registered user MCP server ccdex-worker"
