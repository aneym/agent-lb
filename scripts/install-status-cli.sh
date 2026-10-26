#!/bin/sh

set -eu
runtime=${AGENT_LB_RUNTIME_DIR:-"$HOME/.agent-lb/runtime/agent-lb"}
bindir=${AGENT_LB_BIN_DIR:-"$HOME/.local/bin"}
entry="$runtime/.venv/bin/agent-lb"
if [ ! -x "$entry" ]; then
    echo "No installed agent-lb runtime at $runtime" >&2
    exit 2
fi
mkdir -p "$bindir"
target="$bindir/agent-lb"
if [ -e "$target" ] && [ ! -L "$target" ]; then
    if ! grep -q 'from app.cli import main' "$target"; then
        echo "Refusing to replace unmanaged entrypoint $target" >&2
        exit 2
    fi
fi
ln -sfn "$entry" "$target"
echo "Installed runtime venv entrypoint $target"
