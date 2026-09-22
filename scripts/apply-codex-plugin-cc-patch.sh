#!/bin/sh
# Run after each `claude plugin update codex-plugin-cc` or reinstall.
# No agent-lb-owned plugin install/update path exists (repository search,
# 2026-09-22). This is an explicit post-update step, not an automatic hook.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
plugin_dir=${CODEX_PLUGIN_CC_DIR:-"$HOME/.agent-lb/plugins/codex-plugin-cc"}
patch_file="$repo_dir/patches/codex-plugin-cc/idle-reap.patch"
if [ ! -d "$plugin_dir/plugins/codex/scripts" ]; then
    echo "ERROR: plugin scripts not found in $plugin_dir" >&2
    exit 1
fi
# git apply without --reject is atomic across the patch. Do not use --3way,
# --unsafe-paths, or whitespace relaxation to conceal upstream drift.
if git -C "$plugin_dir" apply --reverse --check "$patch_file" 2>/dev/null; then
    echo "Already patched: $plugin_dir"
    exit 0
fi
if ! git -C "$plugin_dir" apply --check "$patch_file"; then
    echo "ERROR: codex-plugin-cc patch is incompatible in $plugin_dir; file/hunk diagnostics above. No files changed. Rebase the repo-owned patch on the updated plugin." >&2
    exit 1
fi
if ! git -C "$plugin_dir" apply "$patch_file"; then
    echo "ERROR: failed to apply codex-plugin-cc patch in $plugin_dir; see file/hunk diagnostics above." >&2
    exit 1
fi
echo "Patched: $plugin_dir"
