#!/bin/bash
set -u
SRC="${AGENT_LB_SYNC_SRC:-/Volumes/StudioExt/repos/agent-lb}"
DST="${AGENT_LB_RUNTIME_DIR:-$HOME/.agent-lb/runtime/agent-lb}"
ROOT="$(dirname "$DST")"
LOG="$ROOT/sync.log"
LOCKDIR="$ROOT/.sync.lock"
UV="${AGENT_LB_SYNC_UV:-/opt/homebrew/bin/uv}"
mkdir -p "$ROOT"
ts() { date -u +%FT%TZ; }

refuse() {
  echo "$(ts) sync REFUSED: $1" >> "$LOG"
  echo "sync-runtime: REFUSED: $1" >&2
  exit 1
}

if ! perl -e 'alarm 5; exit((-f $ARGV[0]) ? 0 : 1)' "$SRC/pyproject.toml" 2>/dev/null; then
  refuse "$SRC unreachable; runtime left as is"
fi

perl -e 'alarm 60; exec @ARGV' git -C "$SRC" fetch --quiet origin main >/dev/null 2>&1 \
  || refuse "could not fetch origin main into $SRC; its origin/main may be stale"
branch=$(git -C "$SRC" symbolic-ref --quiet --short HEAD 2>/dev/null || echo "")
head=$(git -C "$SRC" rev-parse --verify --quiet HEAD 2>/dev/null || echo "")
main=$(git -C "$SRC" rev-parse --verify --quiet refs/remotes/origin/main 2>/dev/null || echo "")
[ -n "$head" ] || refuse "$SRC is not a git checkout"
[ -n "$main" ] || refuse "$SRC has no origin/main ref"
[ "$branch" = "main" ] || refuse "$SRC is on '${branch:-detached HEAD}', not main"
[ "$head" = "$main" ] || refuse "$SRC HEAD ${head:0:10} is not origin/main ${main:0:10}; fetch and fast-forward first"
git -C "$SRC" diff --quiet HEAD -- 2>/dev/null || refuse "$SRC has uncommitted changes to tracked files"

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  if [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
    rmdir "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null || exit 0
  else
    echo "$(ts) sync SKIPPED: another sync in flight" >> "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

run_rsync() {
  rsync -a --delete \
    --exclude .git --exclude .venv --exclude .agent-lb \
    --exclude frontend/node_modules --exclude clients/macos-menubar/.build \
    --exclude tests --exclude .pytest_cache --exclude .ruff_cache \
    --exclude __pycache__ --exclude .DS_Store \
    "$SRC/" "$DST/" >> "$LOG" 2>&1
}

run_rsync
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "$(ts) rsync failed rc=$rc; retrying in 10s" >> "$LOG"
  sleep 10
  run_rsync
  rc=$?
fi
if [ "$rc" -ne 0 ]; then
  refuse "rsync failed rc=$rc after retry; runtime may be partial"
fi

STAMP="$DST/.venv/.uv-lock-sha"
NEW=$(shasum -a 256 "$DST/uv.lock" | cut -d' ' -f1)
if [ ! -x "$DST/.venv/bin/agent-lb" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$NEW" ]; then
  if (cd "$DST" && "$UV" sync --frozen) >> "$LOG" 2>&1; then
    mkdir -p "$DST/.venv"
    echo "$NEW" > "$STAMP"
    echo "$(ts) venv rebuilt (uv.lock changed)" >> "$LOG"
  else
    echo "$(ts) uv sync FAILED; keeping previous venv" >> "$LOG"
  fi
fi
echo "$(ts) sync ok ${head:0:10}" >> "$LOG"
