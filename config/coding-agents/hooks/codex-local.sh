#!/bin/bash
# Repo edits go through seat-run gpt-implementer / seat-exec, never codex-companion --write. The wrapper refuses repo paths outside the lane directory unless --allow-repo-write explicitly overrides the rule.
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
# Render the same lane boundary for previews and real companion prompts.
prompt_file=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ ${args[i]} == --prompt-file ]]; then
    prompt_file=${args[i+1]:-}
  elif [[ ${args[i]} == --prompt-file=* ]]; then
    prompt_file=${args[i]#--prompt-file=}
  fi
done
if [[ $dry_run == 1 ]]; then
  if [[ -n $prompt_file ]]; then
    python3 "$(dirname "$0")/seat-finish.py" --prompt-file "$prompt_file"
  else
    python3 "$(dirname "$0")/seat-finish.py"
  fi
fi
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
# Admission owns the wait. Re-enter only as its child, then capture immediately before launch.
if [[ ${LOCAL_SEAT_ADMITTED:-0} != 1 && -x "$HOME/.local/bin/verify-slot" ]]; then
  exec "$HOME/.local/bin/verify-slot" codex-local -- env LOCAL_SEAT_ADMITTED=1 bash "$0" "$@"
fi
unset LOCAL_SEAT_ADMITTED
if [[ -n $prompt_file ]]; then
  rendered_prompt=$(mktemp "${TMPDIR:-/tmp}/codex-local-contract.XXXXXX")
  cat "$prompt_file" > "$rendered_prompt"
  printf '\n\n' >> "$rendered_prompt"
  python3 "$(dirname "$0")/seat-finish.py" --prompt-file "$prompt_file" >> "$rendered_prompt"
  for ((i=0; i<${#args[@]}; i++)); do
    if [[ ${args[i]} == --prompt-file ]]; then
      args[i+1]=$rendered_prompt
    elif [[ ${args[i]} == --prompt-file=* ]]; then
      args[i]="--prompt-file=$rendered_prompt"
    fi
  done
  set -- "${args[@]}"
fi
# Capture host telemetry before the worker; the sandbox never calls WindowServer.
focus_dir=$(mktemp -d "${TMPDIR:-/tmp}/codex-local-focus.XXXXXX")
export LOCAL_SEAT_FOCUS_BASELINE="$focus_dir/baseline.json"
python3 "$(dirname "$0")/local-focus-snapshot.py" "$LOCAL_SEAT_FOCUS_BASELINE"
export LOCAL_SEAT_FOCUS_HELPER="$(dirname "$0")/local-focus-snapshot.py"
cp "$(dirname "$0")/local-seat-bin/desktop-guard-focus" "$focus_dir/desktop-guard-focus"
chmod +x "$focus_dir/desktop-guard-focus"
export PATH="$focus_dir:$PATH"
printf 'codex-local: host baseline %s; sandbox snapshot: python3 %s --baseline %s snapshot (non-live; lead compares after run)\n' "$LOCAL_SEAT_FOCUS_BASELINE" "$LOCAL_SEAT_FOCUS_HELPER" "$LOCAL_SEAT_FOCUS_BASELINE" >&2
companion="$HOME/.agent-lb/plugins/codex-plugin-cc/plugins/codex/scripts/codex-companion.mjs"
exec node "$companion" "$@"
