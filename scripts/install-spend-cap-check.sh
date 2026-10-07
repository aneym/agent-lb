#!/bin/sh
# Install the spend-cap scenario from the landed ref, without restarting agent-lb.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bindir=${AGENT_LB_BIN_DIR:-"$HOME/.agent-lb/bin"}
mkdir -p "$bindir"
tmp=$(mktemp "$bindir/.lb-spend-cap-check.XXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
git -C "$repo" show origin/main:scripts/lb-spend-cap-check > "$tmp"
chmod 755 "$tmp"
mv -f "$tmp" "$bindir/lb-spend-cap-check"
echo "Installed spend-cap check from agent-lb origin/main (no restart)"
