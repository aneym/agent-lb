#!/usr/bin/env bash
set -euo pipefail

usage() { echo "usage: $0 [--ref <git ref>] [--link <client>]... [--print]" >&2; exit 2; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
RELEASES="${AGENT_LB_CLIENT_RELEASES:-$HOME/.agent-lb/client-releases}"
BIN_DIR="${AGENT_LB_CLIENT_BIN_DIR:-$HOME/.local/bin}"
CLIENTS=(claude-lb-launch cc fable opus agent-defs-doctor opus-runtime-doctor)
BUNDLE_FILES=("${CLIENTS[@]}" agent_defs_models.py)
ref=origin/main
links=()
print=0
while (( $# )); do
  case "$1" in
    --ref) [[ $# -ge 2 ]] || usage; ref="$2"; shift 2 ;;
    --link) [[ $# -ge 2 ]] || usage; links+=("$2"); shift 2 ;;
    --print) print=1; shift ;;
    *) usage ;;
  esac
done

sha="$(git -C "$REPO_DIR" rev-parse --verify "$ref^{commit}")"
release="$RELEASES/main-${sha:0:8}"
for name in "${links[@]+"${links[@]}"}"; do
  [[ " ${CLIENTS[*]} " == *" $name "* ]] || { echo "error: $name is not a released client" >&2; exit 2; }
done
if (( print )); then
  echo "cut $release from ${sha:0:8} ($ref)"
  for name in "${links[@]+"${links[@]}"}"; do echo "link $BIN_DIR/$name -> $release/$name"; done
  exit 0
fi

if [[ ! -d "$release" ]]; then
  mkdir -p "$RELEASES"
  stage="$(mktemp -d "$RELEASES/.cut-${sha:0:8}.XXXXXX")"
  trap 'rm -rf "$stage"' EXIT
  git -C "$REPO_DIR" archive "$sha" "${BUNDLE_FILES[@]/#/clients/}" | tar -x -C "$stage"
  mv "$stage/clients/"* "$stage/"
  rmdir "$stage/clients"
  {
    echo "commit ${sha:0:8} $(date -u +%Y-%m-%dT%H:%M:%SZ) cut by scripts/cut-client-release.sh from $ref"
    (cd "$stage" && shasum -a 256 "${BUNDLE_FILES[@]}")
  } > "$stage/RELEASE"
  chmod 755 "$stage"
  mv "$stage" "$release"
  trap - EXIT
  echo "cut $release"
else
  (cd "$release" && tail -n +2 RELEASE | shasum -a 256 -c --quiet) || { echo "error: $release does not match its RELEASE" >&2; exit 1; }
  echo "kept $release"
fi

mkdir -p "$BIN_DIR"
for name in "${links[@]+"${links[@]}"}"; do
  tmp="$BIN_DIR/.$name.cut.$$"
  ln -sfn "$release/$name" "$tmp"
  mv -f "$tmp" "$BIN_DIR/$name"
  echo "linked $BIN_DIR/$name -> $release/$name"
done
