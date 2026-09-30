#!/bin/sh
set -eu
prefix="${HOME}/.local/bin"
case "$#" in
    0) ;;
    2) if [ "$1" = "--prefix" ]; then prefix="$2"; else echo "Usage: $0 [--prefix DIR]" >&2; exit 2; fi ;;
    *) echo "Usage: $0 [--prefix DIR]" >&2; exit 2 ;;
esac
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$prefix"
for name in of open-factory; do
    target="$prefix/$name"
    if [ -e "$target" ] || [ -L "$target" ]; then
        if [ ! -L "$target" ] || [ "$(readlink "$target")" != "$root/bin/$name" ]; then
            echo "Refusing to replace existing $target" >&2
            exit 1
        fi
    else
        ln -s "$root/bin/$name" "$target"
    fi
done
printf 'Installed of and open-factory in %s\nNext: of doctor\n' "$prefix"
