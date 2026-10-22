#!/bin/bash


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

review=0
args=()
for arg in "$@"; do
  if [[ $arg == --review ]]; then
    review=1
  else
    args+=("$arg")
  fi
done
set -- "${args[@]}"
prompt_file=""
for ((i=0; i<${#args[@]}; i++)); do
  if [[ ${args[i]} == --prompt-file ]]; then
    prompt_file=${args[i+1]:-}
  elif [[ ${args[i]} == --prompt-file=* ]]; then
    prompt_file=${args[i]#--prompt-file=}
  fi
done
account_args=()
if [[ $review == 1 ]]; then
  for arg in "$@"; do
    case "$arg" in
      --resume|--resume-last|--write|--allow-repo-write) echo 'codex-local: review requires a fresh read-only session' >&2; exit 2 ;;
    esac
  done
  if [[ -z $prompt_file ]]; then
    echo 'codex-local: review requires an explicit non-empty --prompt-file brief' >&2
    exit 2
  fi
  account_args=(--require-owner)
  python3 "$(dirname "$0")/codex-local-account.py" --prompt-file "$prompt_file" --require-owner --validate-only
  args+=(--fresh)
  set -- "${args[@]}"
fi
main_checkout() {
  local dirs
  dirs=$(env -u GIT_DIR -u GIT_WORK_TREE git -C "$1" rev-parse --path-format=absolute --git-dir --git-common-dir 2>/dev/null) || return 1
  [[ $(sed -n 1p <<<"$dirs") == "$(sed -n 2p <<<"$dirs")" ]]
}
write=0
for arg in "$@"; do
  case "$arg" in
    --write) write=1 ;;
    --allow-repo-write) write=0; break ;;
  esac
done
if [[ $write == 1 ]] && main_checkout "$PWD"; then
  if [[ -n $prompt_file ]]; then
    prompt_file=$(cd "$(dirname "$prompt_file")" && pwd)/$(basename "$prompt_file")
    for ((i=0; i<${#args[@]}; i++)); do
      if [[ ${args[i]} == --prompt-file ]]; then
        args[i+1]=$prompt_file
      elif [[ ${args[i]} == --prompt-file=* ]]; then
        args[i]="--prompt-file=$prompt_file"
      fi
    done
    set -- "${args[@]}"
  fi
  checkout=$PWD
  if [[ -n ${LANE_DIR:-} && -d $LANE_DIR ]] && ! main_checkout "$LANE_DIR"; then
    cd "$LANE_DIR"
  else
    cd "$(mktemp -d "${TMPDIR:-/tmp}/codex-local-cwd.XXXXXX")"
  fi
  printf 'codex-local: %s is a shared git checkout; --write runs from %s and the checkout stays read-only\n' "$checkout" "$PWD" >&2
fi
if [[ $dry_run == 1 ]]; then
  if [[ -n $prompt_file ]]; then
    python3 "$(dirname "$0")/seat-finish.py" --prompt-file "$prompt_file"
  else
    python3 "$(dirname "$0")/seat-finish.py"
  fi
fi
if [[ $dry_run == 1 ]]; then
  printf 'host=studio background=false\n'
  printf 'cwd=%s\n' "$PWD"
  printf 'BOX_EXEC_HOST=studio SEAT_BOX=studio PLACEMENT=studio-only node codex-companion.mjs'
  printf ' %q' "$@"
  printf '\n'
  exit 0
fi
if [[ $(uname -s) != Darwin || $(hostname -s | tr '[:upper:]' '[:lower:]') != *studio* ]]; then
  echo 'codex-local: requires Studio; refusing to run or forward on this host' >&2
  exit 75
fi
companion="$HOME/.agent-lb/plugins/codex-plugin-cc/plugins/codex/scripts/codex-companion.mjs"
node "$companion" "$@" --dry-run >&2
export BOX_EXEC_HOST=studio SEAT_BOX=studio PLACEMENT=studio-only
export CODEX_LOCAL_WRITE_SANDBOX=danger-full-access

if [[ -n $prompt_file ]]; then
  contract_name=$(basename "$prompt_file")
  [[ $contract_name == *.contract.* ]] || { echo 'codex-local: prompt path must be a job-key-bound contract' >&2; exit 2; }
  k=${contract_name%%.contract.*}
  [[ $k =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || { echo 'codex-local: invalid contract job key' >&2; exit 2; }
  rendered_prompt=$(mktemp "${TMPDIR:-/tmp}/$k.contract.XXXXXX")
  printf 'contract=%s\n' "$rendered_prompt" >&2
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
  account_env=$(python3 "$(dirname "$0")/codex-local-account.py" --prompt-file "$prompt_file" --shell-env ${account_args[@]+"${account_args[@]}"})
  if [[ -n $account_env ]]; then
    eval "$account_env"
  fi
fi

focus_dir=$(mktemp -d "${TMPDIR:-/tmp}/codex-local-focus.XXXXXX")
export LOCAL_SEAT_FOCUS_BASELINE="$focus_dir/baseline.json"
python3 "$(dirname "$0")/local-focus-snapshot.py" "$LOCAL_SEAT_FOCUS_BASELINE"
export LOCAL_SEAT_FOCUS_HELPER="$(dirname "$0")/local-focus-snapshot.py"
cp "$(dirname "$0")/local-seat-bin/desktop-guard-focus" "$focus_dir/desktop-guard-focus"
chmod +x "$focus_dir/desktop-guard-focus"
cp "$(dirname "$0")/host-probe.py" "$focus_dir/host-probe"
chmod +x "$focus_dir/host-probe"
export PATH="$focus_dir:$PATH"
printf 'codex-local: host baseline %s is non-live; run host-probe --json for bounded live telemetry; lead runs python3 %s/host-probe.py --json once after the seat and compares readings\n' "$LOCAL_SEAT_FOCUS_BASELINE" "$(dirname "$0")" >&2
companion="$HOME/.agent-lb/plugins/codex-plugin-cc/plugins/codex/scripts/codex-companion.mjs"
exec python3 "$(dirname "$0")/codex-local-launch.py" node "$companion" "$@"
