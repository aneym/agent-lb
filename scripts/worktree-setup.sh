#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
kit=${KIT_ROOT:-"$HOME/.agent-rails/kit-runtime/current"}
exec "$kit/bin/python-seed" --repo "$root" "$@"
