#!/bin/bash
# Studio-only foreground companion entrypoint. Keep admission, but never box placement.
set -euo pipefail
if [[ ${1:-} == --dry-run ]]; then
  shift
  dry_run=1
else
  dry_run=0
fi
for arg in "$@"; do
  case "$arg" in
    --background|--background=*) echo 'codex-local: background jobs are forbidden' >&2; exit 2 ;;
  esac
done
[[ ${1:-} == task ]] || { echo 'usage: codex-local.sh [--dry-run] task [companion options]' >&2; exit 2; }
if [[ $dry_run == 1 ]]; then
  printf 'host=studio background=false\n'
  printf 'BOX_EXEC_HOST=studio SEAT_BOX=studio PLACEMENT=studio-only node codex-companion.mjs'
  printf ' %q' "$@"
  printf '\n'
  exit 0
fi
if [[ $(uname -s) != Darwin || $(hostname -s | tr '[:upper:]' '[:lower:]') != *studio* ]]; then
  echo 'codex-local: requires Studio; refusing to run or forward on this host' >&2
  exit 75
fi
export BOX_EXEC_HOST=studio SEAT_BOX=studio PLACEMENT=studio-only
companion="$HOME/.agent-lb/plugins/codex-plugin-cc/plugins/codex/scripts/codex-companion.mjs"
if [[ -x "$HOME/.local/bin/verify-slot" ]]; then
  exec "$HOME/.local/bin/verify-slot" codex-local -- node "$companion" "$@"
fi
exec node "$companion" "$@"
