#!/bin/bash




set -u
SRC="$HOME/repos/agent-lb"
DST="$HOME/.agent-lb/runtime/agent-lb"
LOG="$HOME/.agent-lb/runtime/sync.log"
LOCKDIR="$HOME/.agent-lb/runtime/.sync.lock"
UV=/opt/homebrew/bin/uv
mkdir -p "$HOME/.agent-lb/runtime"
ts() { date -u +%FT%TZ; }


if ! perl -e 'alarm 5; exit((-f $ARGV[0]) ? 0 : 1)' "$SRC/pyproject.toml" 2>/dev/null; then
  echo "$(ts) sync SKIPPED: $SRC unreachable; running last-good runtime" >> "$LOG"
  exit 0
fi


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
  echo "$(ts) sync ALERT: rsync FAILED rc=$rc after retry; running last-good runtime (may be stale)" >> "$LOG"
  exit 0
fi


STAMP="$DST/.venv/.uv-lock-sha"
NEW=$(shasum -a 256 "$DST/uv.lock" | cut -d' ' -f1)
if [ ! -x "$DST/.venv/bin/agent-lb" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$NEW" ]; then
  if (cd "$DST" && "$UV" sync --frozen) >> "$LOG" 2>&1; then
    echo "$NEW" > "$STAMP"
    echo "$(ts) venv rebuilt (uv.lock changed)" >> "$LOG"
  else
    echo "$(ts) uv sync FAILED; keeping previous venv" >> "$LOG"
  fi
fi
echo "$(ts) sync ok" >> "$LOG"
