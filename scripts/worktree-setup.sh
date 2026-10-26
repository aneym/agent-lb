#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
exec "${FACTORY_BIN:-$HOME/.agent-rails/factory-runtime/bin}/python-seed" --repo "$root" "$@"
