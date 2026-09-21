#!/bin/sh
# Install a PATH entry for an already provisioned internal runtime; never restart it.
set -eu
runtime=${AGENT_LB_RUNTIME_DIR:-"$HOME/.agent-lb/runtime/agent-lb"}
bindir=${AGENT_LB_BIN_DIR:-"$HOME/.local/bin"}
if [ ! -x "$runtime/.venv/bin/agent-lb" ]; then
    echo "No installed agent-lb runtime at $runtime" >&2
    exit 2
fi
mkdir -p "$bindir"
target="$bindir/agent-lb"
if [ -e "$target" ] || [ -L "$target" ]; then
    echo "Already exists: $target (left unchanged)" >&2
    exit 2
fi
ln -s "$runtime/.venv/bin/agent-lb" "$target"
echo "Installed $target; run agent-lb status"
