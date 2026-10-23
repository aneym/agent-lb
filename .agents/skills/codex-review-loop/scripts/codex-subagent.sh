#!/usr/bin/env bash
set -euo pipefail



















CODEX_ARGS=()
HAS_DIFF_TARGET=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base|--commit)
      CODEX_ARGS+=("$1" "$2"); HAS_DIFF_TARGET=true; shift 2 ;;
    --uncommitted)
      CODEX_ARGS+=("$1"); HAS_DIFF_TARGET=true; shift ;;
    *)
      echo "Warning: Unknown argument '$1' (passed through)" >&2
      CODEX_ARGS+=("$1"); shift ;;
  esac
done

# Reviews commonly outlive the invoking terminal or exceed its output cap, so
# do not add `--ephemeral`: the persistent rollout is the recovery path after
# either failure. Also do not restore the historical `--full-auto` argument;
# Codex CLI 0.147.0 removed it from `exec review`, which already uses the
# configured non-interactive approval and sandbox policy.


if [[ -n "${CODEX_REVIEW_MODEL:-}" ]]; then
  CODEX_ARGS+=("-m" "$CODEX_REVIEW_MODEL")
fi
if [[ -n "${CODEX_REVIEW_REASONING:-}" ]]; then
  CODEX_ARGS+=("-c" "model_reasoning_effort=\"$CODEX_REVIEW_REASONING\"")
fi





if [ -p /dev/stdin ]; then
  if $HAS_DIFF_TARGET; then

    cat > /dev/null
    echo "Note: stdin prompt ignored (incompatible with --base/--commit/--uncommitted in Codex CLI)" >&2
  else
    CODEX_ARGS+=("-")
  fi
fi


set +e
OUTPUT=$(codex exec review "${CODEX_ARGS[@]}" 2>&1)
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -ne 0 ]; then
  echo "Codex review failed (exit $EXIT_CODE):"
  echo "$OUTPUT"
  exit $EXIT_CODE
fi













PARSED=$(echo "$OUTPUT" | awk '
/^codex$/ { buf=""; capturing=1; next }
/^tokens used$/ { capturing=0; next }
capturing { buf = buf $0 "\n" }
END { printf "%s", buf }
')

if [ -n "$PARSED" ]; then
  echo "$PARSED"
else

  echo "$OUTPUT"
fi

exit 0
