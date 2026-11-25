#!/bin/sh

set -eu
bindir=${AGENT_LB_BIN_DIR:-"$HOME/.local/bin"}
mkdir -p "$bindir"
target="$bindir/agent-lb"
if [ -e "$target" ] && [ ! -L "$target" ]; then
    if ! grep -q 'AGENT_LB_REMOTE_HOST' "$target" && ! grep -q 'from app.cli import main' "$target"; then
        echo "Refusing to replace unmanaged entrypoint $target" >&2
        exit 2
    fi
fi
temporary=$(mktemp "$bindir/.agent-lb.XXXXXX")
printf '%s\n' '#!/bin/sh' >"$temporary"
cat >>"$temporary" <<'EOF'
for runtime in "${AGENT_LB_RUNTIME_DIR:-}" "$HOME/.agent-lb/runtime/agent-lb" "$HOME/.agent-lb-primary/runtime/agent-lb"; do
    if [ -n "$runtime" ] && [ -x "$runtime/.venv/bin/agent-lb" ]; then
        exec "$runtime/.venv/bin/agent-lb" "$@"
    fi
done
host=${AGENT_LB_REMOTE_HOST:-ax42}
if [ "$host" = none ]; then
    echo "agent-lb: no runtime on this machine; install one or set AGENT_LB_REMOTE_HOST to the serving box" >&2
    exit 2
fi
command='AGENT_LB_REMOTE_HOST=none exec "$HOME/.local/bin/agent-lb"'
for arg in "$@"; do
    command="$command '$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")'"
done
exec ssh -T -o BatchMode=yes "$host" "$command"
EOF
chmod 755 "$temporary"
mv -f "$temporary" "$target"
echo "Installed agent-lb entrypoint $target"
